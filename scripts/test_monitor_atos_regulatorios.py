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
