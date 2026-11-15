"""Testes locais das regras críticas do parser V10.9."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("monitor_atos_regulatorios.py")
SPEC = importlib.util.spec_from_file_location("monitor_atos_regulatorios", SCRIPT)
PARSER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PARSER)


class ParserV109Tests(unittest.TestCase):
    def test_janela_padrao_tem_sete_datas(self):
        self.assertEqual(len(PARSER.parse_date_list()), 7)

    def test_extrai_colunas_da_tabela_dou(self):
        from xml.etree import ElementTree as ET
        xml = """<article><body><table>
        <tr><th>Código e-MEC</th><th>IES</th><th>Curso</th><th>Grau</th><th>Modalidade</th><th>Vagas</th></tr>
        <tr><td>123456</td><td>Centro Universitário Exemplo</td><td>Administração</td><td>Bacharelado</td><td>Semipresencial</td><td>100</td></tr>
        </table></body></article>"""
        fields = PARSER.extract_fields(PARSER.xml_to_text(ET.fromstring(xml)))
        self.assertEqual(fields["codigo_emec"], "123456")
        self.assertEqual(fields["ies"], "Centro Universitário Exemplo")
        self.assertEqual(fields["curso"], "Administração")
        self.assertEqual(fields["modalidade"], "Semipresencial")


    def test_login_usa_endpoint_oficial_sem_preflight_acessar(self):
        class Cookies:
            def get(self, name):
                return "sessao-teste" if name == "inlabs_session_cookie" else None

        class Response:
            status_code = 200
            url = "https://inlabs.in.gov.br/"
            text = "Minha conta"
            def raise_for_status(self):
                return None

        class FakeSession:
            def __init__(self):
                self.headers = {}
                self.cookies = Cookies()
                self.calls = []
            def post(self, url, **kwargs):
                self.calls.append(("POST", url, kwargs))
                return Response()
            def get(self, url, **kwargs):
                self.calls.append(("GET", url, kwargs))
                raise AssertionError("O fluxo oficial não deve chamar /acessar.php")

        previous_email, previous_password = PARSER.EMAIL, PARSER.PASSWORD
        PARSER.EMAIL, PARSER.PASSWORD = "teste@example.org", "senha-ficticia"
        session = FakeSession()
        try:
            result = PARSER.login(session)
            self.assertEqual(result.status_code, 200)
            self.assertEqual(len(session.calls), 1)
            self.assertEqual(session.calls[0][0], "POST")
            self.assertEqual(session.calls[0][1], "https://inlabs.in.gov.br/logar.php")
            self.assertEqual(session.calls[0][2]["data"], {
                "email": "teste@example.org", "password": "senha-ficticia"
            })
        finally:
            PARSER.EMAIL, PARSER.PASSWORD = previous_email, previous_password

    def test_login_sem_cookie_falha_com_diagnostico_claro(self):
        class Cookies:
            def get(self, name):
                return None
        class Response:
            status_code = 200
            url = "https://inlabs.in.gov.br/"
            text = "Página de manutenção"
            def raise_for_status(self):
                return None
        class FakeSession:
            def __init__(self):
                self.headers = {}
                self.cookies = Cookies()
            def post(self, url, **kwargs):
                return Response()

        previous_email, previous_password = PARSER.EMAIL, PARSER.PASSWORD
        PARSER.EMAIL, PARSER.PASSWORD = "teste@example.org", "senha-ficticia"
        try:
            with self.assertRaisesRegex(RuntimeError, "cookie de sessão"):
                PARSER.login(FakeSession())
        finally:
            PARSER.EMAIL, PARSER.PASSWORD = previous_email, previous_password

    def test_classifica_http_404_como_endpoint_indisponivel(self):
        class Response:
            status_code = 404
        class HttpError(Exception):
            response = Response()
        result = PARSER.login_failure_details(HttpError("HTTP 404"))
        self.assertEqual(result["status"], "endpoint_indisponivel")
        self.assertEqual(result["categoria"], "endpoint_nao_encontrado")
        self.assertEqual(result["http_status"], 404)

    def test_classifica_http_5xx_como_fonte_indisponivel(self):
        class Response:
            status_code = 502
        class HttpError(Exception):
            response = Response()
        result = PARSER.login_failure_details(HttpError("HTTP 502"))
        self.assertEqual(result["status"], "fonte_indisponivel")
        self.assertEqual(result["categoria"], "erro_servidor")

    def test_nao_confunde_http_401_com_indisponibilidade(self):
        class Response:
            status_code = 401
        class HttpError(Exception):
            response = Response()
        result = PARSER.login_failure_details(HttpError("HTTP 401"))
        self.assertEqual(result["status"], "credenciais_rejeitadas")

    def test_integracao_preserva_confirmados_e_mantem_pendentes(self):
        with tempfile.TemporaryDirectory() as folder:
            previous_path = PARSER.MONITORING
            PARSER.MONITORING = Path(folder)
            try:
                (Path(folder) / "atos_regulatorios.json").write_text(
                    json.dumps({"atos": [
                        {"id": "confirmado-1", "confirmado": True},
                        {"id": "pendente-antigo", "confirmado": False},
                    ]}),
                    encoding="utf-8",
                )
                candidate = {
                    "id": "novo-ato", "titulo": "Portaria teste",
                    "tipo_ato_classificado": "RECONHECIMENTO DE CURSO",
                    "campos_extraidos": {"curso": "Administração"},
                    "edicao": {"data": "2026-11-06"},
                    "confianca": {"score": 90}, "fonte": {},
                }
                result = PARSER.update_radar(
                    {"executed_at": "2026-11-06T12:00:00Z",
                     "resumo": {"xmls_processados": 1}, "downloads": [], "erros": []},
                    [candidate], [],
                )
                by_id = {x["id"]: x for x in result["atos"]}
                self.assertTrue(by_id["confirmado-1"]["confirmado"])
                self.assertFalse(by_id["novo-ato"]["confirmado"])
                self.assertIn("pendente-antigo", by_id)
            finally:
                PARSER.MONITORING = previous_path


if __name__ == "__main__":
    unittest.main()
