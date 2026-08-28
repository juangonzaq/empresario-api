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

# Sube cuando cambia lo que se extrae: obliga a re-leer todos los XML.
PARSER_VERSION = "3"

# Una factura de distribuidora trae cientos de líneas; para cruzar con un
# kardex hacen falta todas, no las doce primeras.
MAX_ITEMS = 500


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


def _str(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def _line_item(line: ET.Element) -> dict:
    """Una línea del comprobante con lo que hace falta para valorizar un
    kardex: qué es (código y descripción), cuánto (cantidad y unidad), a
    cuánto (valor unitario sin IGV y precio con IGV) y cómo tributa.

    ``amount`` es el valor de venta de la línea sin IGV (LineExtensionAmount),
    que es la base del costo; ``tax`` es el IGV de la línea y ``affectation``
    el código del catálogo 07 (10 = gravado, 20 = exonerado, 30 = inafecto)."""
    description = code = ""
    for item in _iter_local(line, "Item"):
        description = _text(next(_iter_local(item, "Description"), None))
        for ident in _iter_local(item, "SellersItemIdentification"):
            code = _text(next(_iter_local(ident, "ID"), None))
            break
        break
    quantity_node = next(
        (n for n in line.iter() if _local(n.tag).endswith("Quantity")), None
    )
    unit = quantity_node.get("unitCode", "") if quantity_node is not None else ""
    amount = _decimal(_text(next(_iter_local(line, "LineExtensionAmount"), None)))

    # cac:Price/cbc:PriceAmount = valor unitario sin impuestos; el precio con
    # IGV va en PricingReference con PriceTypeCode 01.
    unit_value = None
    for price in _iter_local(line, "Price"):
        unit_value = _decimal(_text(next(_iter_local(price, "PriceAmount"), None)))
        break
    unit_price = None
    for ref in _iter_local(line, "PricingReference"):
        for alt in _iter_local(ref, "AlternativeConditionPrice"):
            if _text(next(_iter_local(alt, "PriceTypeCode"), None)) == "01":
                unit_price = _decimal(_text(next(_iter_local(alt, "PriceAmount"), None)))
                break
        break

    tax = None
    affectation = ""
    for tax_total in _iter_local(line, "TaxTotal"):
        tax = _decimal(_text(next(_iter_local(tax_total, "TaxAmount"), None)))
        for cat in _iter_local(tax_total, "TaxCategory"):
            affectation = _text(next(_iter_local(cat, "TaxExemptionReasonCode"), None))
            break
        break

    return {
        "code": fix_mojibake(code)[:60],
        "description": fix_mojibake(description)[:300],
        "quantity": _text(quantity_node) or None,
        "unit": unit[:10],
        "unit_value": _str(unit_value),
        "unit_price": _str(unit_price),
        "amount": _str(amount),
        "tax": _str(tax),
        "affectation": affectation[:4],
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

    # Vencimiento y orden de compra: cabecera UBL, útiles para cobranza y
    # para cruzar la compra con lo pedido.
    due_date = ""
    for child in root:
        if _local(child.tag) == "DueDate":
            due_date = _text(child)
            break
    order_reference = ""
    for ref in _iter_local(root, "OrderReference"):
        order_reference = fix_mojibake(_text(next(_iter_local(ref, "ID"), None)))
        break

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
        "due_date": due_date[:10] or None,
        "order_reference": order_reference[:60],
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


def extract_pending(force: bool = False, account_ruc: str | None = None) -> dict[str, int]:
    """Parse every invoice whose extract is missing or stale.

    ``account_ruc`` acota a una empresa: así corre al final de cada
    sincronización y el detalle (ítems, IGV, forma de pago) aparece con el
    comprobante, no cuando pase la tarea global."""
    done = failed = skipped = 0
    invoices = ElectronicInvoice.objects.all().select_related("extract")
    if account_ruc:
        invoices = invoices.filter(account_ruc=account_ruc)
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
