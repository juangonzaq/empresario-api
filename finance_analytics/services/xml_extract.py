"""UBL 2.1 parser: turns each comprobante's signed XML into normalized fields.

Namespace-agnostic on purpose (matches by local tag name): SUNAT emitters vary
in prefixes but not in UBL structure. Results are cached per ``xml_sha256`` +
parser version, so re-runs only pay for changed documents.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation
from typing import Any

from sunat_cpe.models import ElectronicInvoice

from ..models import ExtractStatus, InvoiceExtract

logger = logging.getLogger(__name__)

PARSER_VERSION = "2"

MAX_ITEMS = 12


def fix_mojibake(text: str) -> str:
    """Repair UTF-8 read as Latin-1/Windows-1252 (``COMPAÃ\x91IA`` → ``COMPAÑIA``).

    Only applied when the telltale marker is present and a round-trip
    succeeds; clean strings pass through untouched. Both single-byte codecs
    are tried because SUNAT sources mix them (0x91 shows as a control char in
    Latin-1 but as ``‘`` in Windows-1252).
    """
    if not text or "Ã" not in text:
        return text
    for codec in ("latin-1", "cp1252"):
        try:
            return text.encode(codec).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
    return text


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _strip_signature(xml_text: str) -> str:
    """The ds:Signature block is huge and irrelevant for analytics."""
    return re.sub(r"<ds:Signature.*?</ds:Signature>", "", xml_text, flags=re.S)


def _decimal(text: str | None) -> Decimal | None:
    if text is None:
        return None
    try:
        return Decimal(text.strip())
    except (InvalidOperation, ValueError, AttributeError):
        return None


def _find_first(root: ET.Element, path: list[str]) -> ET.Element | None:
    """Walk ``path`` of local names, taking the first match at each level."""
    node = root
    for name in path:
        node = next((c for c in node if _local(c.tag) == name), None)
        if node is None:
            return None
    return node


def _text(node: ET.Element | None) -> str:
    return (node.text or "").strip() if node is not None else ""


def _iter_local(root: ET.Element, name: str):
    for node in root.iter():
        if _local(node.tag) == name:
            yield node


def _party_address(party_root: ET.Element | None) -> str:
    if party_root is None:
        return ""
    for addr in _iter_local(party_root, "RegistrationAddress"):
        parts = []
        for line in _iter_local(addr, "Line"):
            parts.append(_text(line))
        for name in ("District", "CityName", "CountrySubentity"):
            node = next(_iter_local(addr, name), None)
            value = _text(node)
            if value:
                parts.append(value)
        return fix_mojibake(", ".join(p for p in parts if p))[:255]
    return ""


def _tax_amounts(root: ET.Element) -> tuple[Decimal | None, Decimal | None]:
    """(base imponible, IGV) del primer TaxSubtotal con código 1000 (IGV)."""
    fallback: tuple[Decimal | None, Decimal | None] = (None, None)
    for subtotal in _iter_local(root, "TaxSubtotal"):
        taxable = _decimal(_text(next(_iter_local(subtotal, "TaxableAmount"), None)))
        tax = _decimal(_text(next(_iter_local(subtotal, "TaxAmount"), None)))
        scheme_id = _text(_find_first(subtotal, ["TaxCategory", "TaxScheme", "ID"]))
        if scheme_id == "1000":
            return taxable, tax
        if fallback == (None, None):
            fallback = (taxable, tax)
    return fallback


def _payment_form_entry(
    terms: ET.Element, means: str, amount: Decimal | None
) -> tuple[str | None, dict | None]:
    """Interpret one FormaPago block: either the form itself or a cuota."""
    if means in ("Contado", "Credito"):
        return means, None
    if means.startswith("Cuota"):
        due = _text(next(_iter_local(terms, "PaymentDueDate"), None))
        return None, {
            "amount": str(amount) if amount is not None else None,
            "due_date": due or None,
        }
    return None, None


def _payment_terms(root: ET.Element) -> tuple[str, list[dict], dict | None]:
    """(FormaPago, cuotas, detracción) desde los bloques PaymentTerms."""
    form = ""
    installments: list[dict] = []
    detraction: dict | None = None
    for terms in _iter_local(root, "PaymentTerms"):
        terms_id = _text(next(_iter_local(terms, "ID"), None))
        means = _text(next(_iter_local(terms, "PaymentMeansID"), None))
        amount = _decimal(_text(next(_iter_local(terms, "Amount"), None)))
        if terms_id == "FormaPago":
            new_form, installment = _payment_form_entry(terms, means, amount)
            form = new_form or form
            if installment:
                installments.append(installment)
        elif terms_id == "Detraccion":
            percent = _decimal(_text(next(_iter_local(terms, "PaymentPercent"), None)))
            detraction = {
                "percent": str(percent) if percent is not None else None,
                "amount": str(amount) if amount is not None else None,
            }
    return form, installments, detraction


def _lines(root: ET.Element) -> list[dict]:
    items = []
    for line in _iter_local(root, "InvoiceLine"):
        items.append(_line_item(line))
    if not items:  # CreditNoteLine / DebitNoteLine en notas
        for name in ("CreditNoteLine", "DebitNoteLine"):
            for line in _iter_local(root, name):
                items.append(_line_item(line))
    return items[:MAX_ITEMS]


def _line_item(line: ET.Element) -> dict:
    description = ""
    for item in _iter_local(line, "Item"):
        description = _text(next(_iter_local(item, "Description"), None))
        break
    quantity_node = next(
        (n for n in line.iter() if _local(n.tag).endswith("Quantity")), None
    )
    amount = _decimal(_text(next(_iter_local(line, "LineExtensionAmount"), None)))
    return {
        "description": fix_mojibake(description)[:200],
        "quantity": _text(quantity_node) or None,
        "amount": str(amount) if amount is not None else None,
    }


def parse_invoice_xml(xml_text: str) -> dict[str, Any]:
    """Extract the normalized fields from one UBL document."""
    root = ET.fromstring(_strip_signature(xml_text))

    currency = _text(next(_iter_local(root, "DocumentCurrencyCode"), None))
    total = None
    for monetary in _iter_local(root, "LegalMonetaryTotal"):
        total = _decimal(_text(next(_iter_local(monetary, "PayableAmount"), None)))
        break
    if total is None:  # Notas usan RequestedMonetaryTotal
        for monetary in _iter_local(root, "RequestedMonetaryTotal"):
            total = _decimal(_text(next(_iter_local(monetary, "PayableAmount"), None)))
            break

    taxable, igv = _tax_amounts(root)
    form, installments, detraction = _payment_terms(root)

    reference_id = ""
    for ref in _iter_local(root, "BillingReference"):
        reference_id = _text(_find_first(ref, ["InvoiceDocumentReference", "ID"]))
        break
    reason_code = reason = ""
    for disc in _iter_local(root, "DiscrepancyResponse"):
        reason_code = _text(next(_iter_local(disc, "ResponseCode"), None))
        reason = fix_mojibake(_text(next(_iter_local(disc, "Description"), None)))
        break

    supplier = next(_iter_local(root, "AccountingSupplierParty"), None)
    customer = next(_iter_local(root, "AccountingCustomerParty"), None)

    notes = []
    for child in root:
        if _local(child.tag) == "Note":
            value = fix_mojibake(_text(child))
            if value:
                notes.append(value[:300])

    return {
        "currency": currency[:3],
        "total_amount": total,
        "taxable_amount": taxable,
        "igv_amount": igv,
        "payment_form": form,
        "installments": installments,
        "detraction": detraction,
        "reference_id": reference_id[:40],
        "reference_reason_code": reason_code[:4],
        "reference_reason": reason[:255],
        "items": _lines(root),
        "supplier_address": _party_address(supplier),
        "customer_address": _party_address(customer),
        "notes": notes[:5],
    }


def _fingerprint(invoice: ElectronicInvoice) -> str:
    return f"{invoice.xml_sha256 or 'none'}:{PARSER_VERSION}"


def extract_invoice(invoice: ElectronicInvoice) -> InvoiceExtract:
    extract, _ = InvoiceExtract.objects.get_or_create(invoice=invoice)
    extract.fingerprint = _fingerprint(invoice)
    if not invoice.xml_content:
        extract.status = ExtractStatus.NO_XML
        extract.save()
        return extract
    try:
        data = parse_invoice_xml(invoice.xml_content)
        for field, value in data.items():
            setattr(extract, field, value)
        extract.status = ExtractStatus.DONE
        extract.error = ""
    except Exception as exc:  # the stored failure is the diagnostic
        logger.exception("XML extract failed for invoice %s", invoice.pk)
        extract.status = ExtractStatus.FAILED
        extract.error = str(exc)[:2000]
    extract.save()
    return extract


def extract_pending(force: bool = False) -> dict[str, int]:
    """Parse every invoice whose extract is missing or stale."""
    done = failed = skipped = 0
    invoices = ElectronicInvoice.objects.all().select_related("extract")
    for invoice in invoices.iterator():
        extract = getattr(invoice, "extract", None)
        if (
            not force
            and extract is not None
            and extract.status == ExtractStatus.DONE
            and extract.fingerprint == _fingerprint(invoice)
        ):
            skipped += 1
            continue
        result = extract_invoice(invoice)
        if result.status == ExtractStatus.FAILED:
            failed += 1
        else:
            done += 1
    return {"parsed": done, "failed": failed, "skipped": skipped}
