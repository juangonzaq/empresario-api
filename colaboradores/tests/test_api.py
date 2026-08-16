"""El registro de colaboradores: alta a mano, sueldo y enlace con AFPnet."""

from __future__ import annotations

import tempfile
from datetime import date, timedelta
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from rest_framework import status as http

from afpnet.models import AfpnetAffiliate, AfpnetContribution
from colaboradores.models import Colaborador, OrigenSueldo, RegimenPensionario
from core.testing import TenantAPITestCase


def crear_afiliado(ruc: str, **campos) -> AfpnetAffiliate:
    datos = {
        "cuspp": "123456AAAAA1",
        "document_type": "DNI",
        "document_number": "45678912",
        "full_name": "ANA QUISPE ROJAS",
        "afp": "integra",
        "is_active": True,
    }
    return AfpnetAffiliate.objects.create(taxpayer_id=ruc, **{**datos, **campos})


def aportar(afiliado: AfpnetAffiliate, period: str, remuneracion: str | None):
    return AfpnetContribution.objects.create(
        affiliate=afiliado,
        taxpayer_id=afiliado.taxpayer_id,
        period=period,
        remuneration=None if remuneracion is None else Decimal(remuneracion),
    )


class AltaManualTests(TenantAPITestCase):
    """Quien todavía no está en AFP ni en ONP también cobra."""

    def setUp(self):
        self.url = reverse("colaboradores:colaborador-list")

    def alta(self, **datos):
        cuerpo = {
            "full_name": "Luis Vargas Pinto",
            "document_type": "DNI",
            "document_number": "70123456",
            "regimen": RegimenPensionario.SIN_REGIMEN,
            "monthly_salary": "1500.00",
        }
        # JSON y no formulario: el alta admite «todavía no sé el sueldo», y un
        # multipart no sabe escribir null.
        return self.client.post(self.url, {**cuerpo, **datos}, format="json")

    def test_registra_sin_regimen_con_sueldo(self):
        respuesta = self.alta()
        self.assertEqual(respuesta.status_code, http.HTTP_201_CREATED)
        colaborador = Colaborador.objects.get(document_number="70123456")
        self.assertEqual(colaborador.taxpayer_id, self.RUC)
        self.assertEqual(colaborador.monthly_salary, Decimal("1500.00"))
        self.assertEqual(colaborador.salary_source, OrigenSueldo.MANUAL)
        self.assertIsNotNone(colaborador.salary_updated_at)
        self.assertFalse(colaborador.en_afpnet)

    def test_registra_en_onp(self):
        respuesta = self.alta(regimen=RegimenPensionario.ONP, afp="integra")
        self.assertEqual(respuesta.status_code, http.HTTP_201_CREATED)
        # La AFP no significa nada fuera del régimen privado: se limpia en vez
        # de dejar una ficha que se contradice.
        self.assertEqual(respuesta.data["afp"], "")
        self.assertEqual(respuesta.data["regimen_label"], "ONP")

    def test_admite_no_saber_el_sueldo_todavia(self):
        respuesta = self.alta(monthly_salary=None)
        self.assertEqual(respuesta.status_code, http.HTTP_201_CREATED)
        self.assertIsNone(respuesta.data["monthly_salary"])

    def test_rechaza_sueldo_cero(self):
        respuesta = self.alta(monthly_salary="0")
        self.assertEqual(respuesta.status_code, http.HTTP_400_BAD_REQUEST)
        self.assertIn("monthly_salary", respuesta.data)

    def test_rechaza_dni_de_otra_longitud(self):
        respuesta = self.alta(document_number="7012345")
        self.assertEqual(respuesta.status_code, http.HTTP_400_BAD_REQUEST)
        self.assertIn("document_number", respuesta.data)

    def test_rechaza_documento_repetido(self):
        self.alta()
        respuesta = self.alta(full_name="Otro Nombre Distinto")
        self.assertEqual(respuesta.status_code, http.HTTP_400_BAD_REQUEST)
        self.assertIn("document_number", respuesta.data)

    def test_no_ve_los_de_otra_empresa(self):
        Colaborador.objects.create(
            taxpayer_id="20601030013", full_name="De otra empresa",
            document_number="11111111",
        )
        self.alta()
        respuesta = self.client.get(self.url)
        documentos = [c["document_number"] for c in respuesta.data]
        self.assertEqual(documentos, ["70123456"])


class SueldoTests(TenantAPITestCase):
    def setUp(self):
        self.colaborador = Colaborador.objects.create(
            taxpayer_id=self.RUC, full_name="Luis Vargas Pinto",
            document_number="70123456", monthly_salary=Decimal("1500"),
        )
        self.url = reverse(
            "colaboradores:colaborador-detail", args=[self.colaborador.id]
        )

    def test_edita_el_sueldo(self):
        respuesta = self.client.patch(self.url, {"monthly_salary": "1800.50"})
        self.assertEqual(respuesta.status_code, http.HTTP_200_OK)
        self.colaborador.refresh_from_db()
        self.assertEqual(self.colaborador.monthly_salary, Decimal("1800.50"))
        self.assertEqual(self.colaborador.salary_source, OrigenSueldo.MANUAL)

    def test_quita_del_registro(self):
        respuesta = self.client.delete(self.url)
        self.assertEqual(respuesta.status_code, http.HTTP_204_NO_CONTENT)
        self.assertFalse(Colaborador.objects.filter(pk=self.colaborador.pk).exists())

    def test_sin_afpnet_no_hay_sueldo_que_traer(self):
        url = reverse(
            "colaboradores:colaborador-sueldo-afpnet", args=[self.colaborador.id]
        )
        respuesta = self.client.post(url)
        self.assertEqual(respuesta.status_code, http.HTTP_409_CONFLICT)
        self.assertEqual(respuesta.data["code"], "sin_afpnet")


class SincronizacionAfpnetTests(TenantAPITestCase):
    """Al listar, quien ya está enrolado en AFPnet aparece con su sueldo."""

    def setUp(self):
        self.afiliado = crear_afiliado(self.RUC)
        self.url = reverse("colaboradores:colaborador-list")

    def listar(self):
        return self.client.get(self.url)

    def test_crea_la_ficha_con_la_ultima_remuneracion(self):
        aportar(self.afiliado, "202603", "2500.00")
        aportar(self.afiliado, "202605", "2800.00")
        aportar(self.afiliado, "202604", "2600.00")

        respuesta = self.listar()
        self.assertEqual(respuesta.status_code, http.HTTP_200_OK)
        self.assertEqual(len(respuesta.data), 1)
        ficha = respuesta.data[0]
        self.assertEqual(ficha["full_name"], "ANA QUISPE ROJAS")
        self.assertEqual(ficha["regimen"], RegimenPensionario.AFP)
        self.assertEqual(ficha["afp_label"], "AFP Integra")
        self.assertTrue(ficha["en_afpnet"])
        self.assertEqual(Decimal(ficha["monthly_salary"]), Decimal("2800.00"))
        self.assertEqual(ficha["salary_source"], OrigenSueldo.AFPNET)
        self.assertEqual(ficha["salary_period"], "202605")

    def test_ignora_los_meses_sin_remuneracion(self):
        """Un mes sin remuneración declarada no es un sueldo de cero."""
        aportar(self.afiliado, "202604", "2600.00")
        aportar(self.afiliado, "202605", None)
        aportar(self.afiliado, "202606", "0")

        ficha = self.listar().data[0]
        self.assertEqual(Decimal(ficha["monthly_salary"]), Decimal("2600.00"))
        self.assertEqual(ficha["salary_period"], "202604")

    def test_sin_historial_queda_sin_sueldo(self):
        ficha = self.listar().data[0]
        self.assertIsNone(ficha["monthly_salary"])

    def test_el_sueldo_escrito_a_mano_sobrevive_a_la_sincronizacion(self):
        aportar(self.afiliado, "202605", "2800.00")
        colaborador_id = self.listar().data[0]["id"]

        detalle = reverse("colaboradores:colaborador-detail", args=[colaborador_id])
        self.client.patch(detalle, {"monthly_salary": "3200.00"})

        ficha = self.listar().data[0]
        self.assertEqual(Decimal(ficha["monthly_salary"]), Decimal("3200.00"))
        self.assertEqual(ficha["salary_source"], OrigenSueldo.MANUAL)

    def test_vuelve_al_sueldo_de_afpnet_cuando_se_pide(self):
        aportar(self.afiliado, "202605", "2800.00")
        colaborador_id = self.listar().data[0]["id"]
        self.client.patch(
            reverse("colaboradores:colaborador-detail", args=[colaborador_id]),
            {"monthly_salary": "3200.00"},
        )

        respuesta = self.client.post(
            reverse("colaboradores:colaborador-sueldo-afpnet", args=[colaborador_id])
        )
        self.assertEqual(respuesta.status_code, http.HTTP_200_OK)
        self.assertEqual(Decimal(respuesta.data["monthly_salary"]), Decimal("2800.00"))
        self.assertEqual(respuesta.data["salary_source"], OrigenSueldo.AFPNET)

    def test_adopta_al_que_ya_estaba_registrado_a_mano(self):
        """Se contrata, se registra sin régimen, semanas después elige AFP.

        Debe quedar una sola ficha —la suya, con su sueldo—, no dos.
        """
        Colaborador.objects.create(
            taxpayer_id=self.RUC, full_name="Ana Quispe",
            document_number=self.afiliado.document_number,
            monthly_salary=Decimal("2000"), regimen=RegimenPensionario.SIN_REGIMEN,
        )
        aportar(self.afiliado, "202605", "2800.00")

        respuesta = self.listar()
        self.assertEqual(len(respuesta.data), 1)
        ficha = respuesta.data[0]
        self.assertEqual(ficha["cuspp"], self.afiliado.cuspp)
        self.assertEqual(ficha["regimen"], RegimenPensionario.AFP)
        # Su sueldo lo escribió la empresa: AFPnet no lo pisa.
        self.assertEqual(Decimal(ficha["monthly_salary"]), Decimal("2000.00"))

    def test_no_cambia_el_regimen_de_quien_esta_en_afpnet(self):
        colaborador_id = self.listar().data[0]["id"]
        respuesta = self.client.patch(
            reverse("colaboradores:colaborador-detail", args=[colaborador_id]),
            {"regimen": RegimenPensionario.ONP},
        )
        self.assertEqual(respuesta.status_code, http.HTTP_400_BAD_REQUEST)
        self.assertIn("regimen", respuesta.data)

    def test_refleja_que_dejo_la_planilla(self):
        self.listar()
        self.afiliado.is_active = False
        self.afiliado.save(update_fields=["is_active"])
        self.assertFalse(self.listar().data[0]["is_active"])


class MemorandumTests(TenantAPITestCase):
    """El control de memorándums que antes vivía en Excel, por colaborador."""

    def setUp(self):
        self.colaborador = Colaborador.objects.create(
            taxpayer_id=self.RUC,
            document_number="45678912",
            full_name="Juana María Pérez Gómez",
            position="Administración",
        )
        self.url = reverse("colaboradores:memorandum-list")

    def emitir(self, **datos):
        cuerpo = {
            "colaborador": str(self.colaborador.id),
            "fecha_emision": "2026-06-10",
            "tipo": "llamada_atencion",
            "asunto": "Tardanzas reiteradas",
            "descripcion": "Tercera tardanza en el mes sin justificación",
            **datos,
        }
        return self.client.post(self.url, cuerpo, format="json")

    def test_emite_con_numero_automatico_correlativo(self):
        primero = self.emitir()
        segundo = self.emitir(asunto="Nueva tardanza")
        self.assertEqual(primero.status_code, http.HTTP_201_CREATED)
        self.assertEqual(primero.data["numero"], "MEMO-2026-001")
        self.assertEqual(segundo.data["numero"], "MEMO-2026-002")
        self.assertEqual(primero.data["colaborador_nombre"], "Juana María Pérez Gómez")
        self.assertEqual(primero.data["tipo_label"], "Llamada de atención")

    def test_respeta_numero_del_excel_y_rechaza_repetidos(self):
        propio = self.emitir(numero="MEMO-2024-001")
        self.assertEqual(propio.data["numero"], "MEMO-2024-001")
        repetido = self.emitir(numero="MEMO-2024-001")
        self.assertEqual(repetido.status_code, http.HTTP_400_BAD_REQUEST)

    def test_el_correlativo_no_choca_tras_borrar_uno_antiguo(self):
        primero = self.emitir()
        self.emitir(asunto="Otro")  # MEMO-2026-002
        self.client.delete(
            reverse("colaboradores:memorandum-detail", args=[primero.data["id"]])
        )
        # El siguiente sale del máximo vigente (002), no del conteo (1): un
        # conteo daría 002 otra vez y chocaría con el que sigue en uso.
        tercero = self.emitir(asunto="Tercero")
        self.assertEqual(tercero.status_code, http.HTTP_201_CREATED)
        self.assertEqual(tercero.data["numero"], "MEMO-2026-003")

    def test_lista_por_colaborador(self):
        otro = Colaborador.objects.create(
            taxpayer_id=self.RUC, document_number="87654321",
            full_name="Pedro Rojas Luna",
        )
        self.emitir()
        self.emitir(colaborador=str(otro.id), asunto="Felicitación", tipo="felicitacion")

        respuesta = self.client.get(self.url, {"colaborador": str(self.colaborador.id)})
        self.assertEqual(len(respuesta.data), 1)
        self.assertEqual(respuesta.data[0]["asunto"], "Tardanzas reiteradas")
        # Un identificador malformado no revienta: simplemente no hay nadie.
        vacia = self.client.get(self.url, {"colaborador": "no-es-uuid"})
        self.assertEqual(vacia.data, [])

    def test_fecha_de_entrega_marca_entregado(self):
        emitido = self.emitir(fecha_entrega="2026-06-10")
        self.assertTrue(emitido.data["entregado"])

    def test_rechaza_firma_sin_entrega(self):
        respuesta = self.emitir(firmado=True)
        self.assertEqual(respuesta.status_code, http.HTTP_400_BAD_REQUEST)

    def test_actualiza_la_entrega(self):
        emitido = self.emitir()
        url = reverse("colaboradores:memorandum-detail", args=[emitido.data["id"]])
        cambiado = self.client.patch(
            url, {"fecha_entrega": "2026-06-11", "firmado": True}, format="json"
        )
        self.assertEqual(cambiado.status_code, http.HTTP_200_OK)
        self.assertTrue(cambiado.data["entregado"])
        self.assertTrue(cambiado.data["firmado"])

    def test_no_acepta_colaborador_de_otra_empresa(self):
        ajeno = Colaborador.objects.create(
            taxpayer_id="20111111111", document_number="11223344",
            full_name="Trabajador Ajeno",
        )
        respuesta = self.emitir(colaborador=str(ajeno.id))
        self.assertEqual(respuesta.status_code, http.HTTP_400_BAD_REQUEST)

    def test_no_ve_los_de_otra_empresa(self):
        self.emitir()
        usuario, _ = self.make_tenant("20111111111", "otra@empresa.pe")
        self.client.force_authenticate(usuario)
        respuesta = self.client.get(self.url)
        self.assertEqual(respuesta.data, [])


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="contratos-test-"))
class ContratoTests(TenantAPITestCase):
    """El control de vencimiento de contratos, con su archivo firmado."""

    def setUp(self):
        self.colaborador = Colaborador.objects.create(
            taxpayer_id=self.RUC,
            document_number="45678912",
            full_name="Juana María Pérez Gómez",
            position="Asistente Contable",
        )
        self.url = reverse("colaboradores:contrato-list")

    def registrar(self, **datos):
        hoy = date.today()
        cuerpo = {
            "colaborador": str(self.colaborador.id),
            "tipo": "sujeto_a_modalidad",
            "causa_objetiva": "Incremento de actividad por apertura de local",
            "fecha_inicio": str(hoy - timedelta(days=30)),
            "fecha_fin": str(hoy + timedelta(days=300)),
            **datos,
        }
        return self.client.post(self.url, cuerpo, format="json")

    def test_registra_y_calcula_lo_derivado(self):
        hoy = date.today()
        respuesta = self.registrar(
            fecha_inicio="2023-06-01",
            fecha_fin=str(hoy + timedelta(days=90)),
        )
        self.assertEqual(respuesta.status_code, http.HTTP_201_CREATED)
        self.assertEqual(respuesta.data["estado"], "vigente")
        self.assertEqual(respuesta.data["dias_para_vencer"], 90)
        self.assertEqual(respuesta.data["colaborador_cargo"], "Asistente Contable")
        self.assertFalse(respuesta.data["tiene_archivo"])

    def test_estados_vencido_y_por_vencer(self):
        hoy = date.today()
        vencido = self.registrar(
            fecha_inicio="2023-06-01", fecha_fin="2024-05-31"
        )
        self.assertEqual(vencido.data["estado"], "vencido")
        self.assertLess(vencido.data["dias_para_vencer"], 0)

        por_vencer = self.registrar(fecha_fin=str(hoy + timedelta(days=15)))
        self.assertEqual(por_vencer.data["estado"], "por_vencer")

    def test_indefinido_no_lleva_fecha_fin(self):
        respuesta = self.registrar(
            tipo="indefinido", causa_objetiva="", fecha_fin=None
        )
        self.assertEqual(respuesta.status_code, http.HTTP_201_CREATED)
        self.assertEqual(respuesta.data["estado"], "indefinido")
        self.assertIsNone(respuesta.data["dias_para_vencer"])

    def test_modalidad_exige_causa_objetiva(self):
        respuesta = self.registrar(causa_objetiva="")
        self.assertEqual(respuesta.status_code, http.HTTP_400_BAD_REQUEST)
        self.assertIn("causa_objetiva", respuesta.data)

    def test_renovacion_manda_sobre_el_vencimiento(self):
        hoy = date.today()
        registrado = self.registrar(
            fecha_inicio="2023-06-01", fecha_fin="2024-05-31"
        ).data
        url = reverse("colaboradores:contrato-detail", args=[registrado["id"]])
        renovado = self.client.patch(
            url,
            {
                "renovar": True,
                "nueva_fecha_fin": str(hoy + timedelta(days=200)),
                "fecha_comunicacion": str(hoy),
            },
            format="json",
        ).data
        self.assertEqual(renovado["estado"], "vigente")
        self.assertEqual(renovado["dias_para_vencer"], 200)

    def test_fechas_incoherentes_se_rechazan(self):
        malo = self.registrar(fecha_inicio="2026-01-01", fecha_fin="2025-01-01")
        self.assertEqual(malo.status_code, http.HTTP_400_BAD_REQUEST)

    def _subir(self, contrato_id, nombre="contrato.pdf", contenido=b"%PDF-1.4 firmado"):
        url = reverse("colaboradores:contrato-archivo", args=[contrato_id])
        return self.client.post(
            url, {"archivo": SimpleUploadedFile(nombre, contenido)},
            format="multipart",
        )

    def test_sube_descarga_y_quita_el_archivo(self):
        contrato_id = self.registrar().data["id"]
        subida = self._subir(contrato_id)
        self.assertEqual(subida.status_code, http.HTTP_200_OK)
        self.assertTrue(subida.data["tiene_archivo"])
        # El storage puede añadir un sufijo si el nombre ya existe en disco;
        # lo que importa es que se conserven base y extensión.
        self.assertTrue(subida.data["archivo_nombre"].startswith("contrato"))
        self.assertTrue(subida.data["archivo_nombre"].endswith(".pdf"))

        url = reverse("colaboradores:contrato-archivo", args=[contrato_id])
        descarga = self.client.get(url)
        self.assertEqual(descarga.status_code, http.HTTP_200_OK)
        self.assertIn("contrato", descarga["Content-Disposition"])
        self.assertEqual(b"".join(descarga.streaming_content), b"%PDF-1.4 firmado")

        quitado = self.client.delete(url)
        self.assertFalse(quitado.data["tiene_archivo"])
        self.assertEqual(self.client.get(url).status_code, http.HTTP_404_NOT_FOUND)

    def test_rechaza_formatos_y_tamanos_fuera_de_lugar(self):
        contrato_id = self.registrar().data["id"]
        exe = self._subir(contrato_id, nombre="contrato.exe")
        self.assertEqual(exe.status_code, http.HTTP_400_BAD_REQUEST)
        gigante = self._subir(
            contrato_id, contenido=b"x" * (10 * 1024 * 1024 + 1)
        )
        self.assertEqual(gigante.status_code, http.HTTP_400_BAD_REQUEST)

    def test_no_ve_ni_descarga_los_de_otra_empresa(self):
        contrato_id = self.registrar().data["id"]
        self._subir(contrato_id)
        usuario, _ = self.make_tenant("20111111111", "otra@empresa.pe")
        self.client.force_authenticate(usuario)
        self.assertEqual(self.client.get(self.url).data, [])
        url = reverse("colaboradores:contrato-archivo", args=[contrato_id])
        self.assertEqual(self.client.get(url).status_code, http.HTTP_404_NOT_FOUND)


class CumpleanosTests(TenantAPITestCase):
    """La fecha de nacimiento: dato de la empresa para el card de cumpleaños."""

    def setUp(self):
        self.colaborador = Colaborador.objects.create(
            taxpayer_id=self.RUC, full_name="Juana María Pérez Gómez",
            document_number="45678912",
        )
        self.url = reverse(
            "colaboradores:colaborador-detail", args=[self.colaborador.id]
        )

    def test_registra_y_expone_la_fecha(self):
        respuesta = self.client.patch(
            self.url, {"birth_date": "1994-09-14"}, format="json"
        )
        self.assertEqual(respuesta.status_code, http.HTTP_200_OK)
        self.assertEqual(respuesta.data["birth_date"], "1994-09-14")

    def test_rechaza_fechas_imposibles(self):
        futura = self.client.patch(
            self.url, {"birth_date": str(date.today() + timedelta(days=1))},
            format="json",
        )
        self.assertEqual(futura.status_code, http.HTTP_400_BAD_REQUEST)
        antigua = self.client.patch(
            self.url, {"birth_date": "1850-01-01"}, format="json"
        )
        self.assertEqual(antigua.status_code, http.HTTP_400_BAD_REQUEST)
