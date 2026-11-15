import unittest
from unittest.mock import patch
from scripts import monitor_dou_public_search as fallback


class PublicSearchFallbackTests(unittest.TestCase):
    def test_parse_results_extracts_official_article_metadata(self):
        html = b"""
        <script id="_br_com_seatecnologia_in_buscadou_BuscaDouPortlet_params" type="application/json">
        {"jsonArray":[{"classPK":123,"title":"Portaria MEC","content":"Reconhece o curso superior","pubDate":"15/11/2026","pubName":"DO1","artType":"Portaria","urlTitle":"portaria-mec-123"}]}
        </script>
        """
        items = fallback.parse_results(html)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], "123")
        self.assertTrue(items[0]["url"].endswith("/portaria-mec-123"))

    def test_authority_search_result_is_recognized_for_full_text_capture(self):
        item = {"title": "Portaria", "hierarchy": "Ministério da Educação/SERES"}
        self.assertTrue(fallback.is_education_authority(item))

    def test_extract_article_text_prefers_article_content(self):
        article_text = (
            "Art. 1 Esta Portaria determina a guarda do acervo acadêmico. "
            "As instituições de educação superior deverão manter os registros "
            "acadêmicos organizados, íntegros e disponíveis para consulta. "
            "O texto integral deve permitir a identificação das obrigações "
            "institucionais, dos prazos aplicáveis e dos procedimentos de "
            "preservação documental, sem confundir o conteúdo normativo com "
            "menus, cabeçalhos ou elementos de navegação da página."
        )
        html = (
            "<html><body><nav>Menu</nav><article><p>"
            + article_text
            + "</p></article></body></html>"
        ).encode("utf-8")
        text = fallback.extract_article_text(html)
        self.assertIn("guarda do acervo acadêmico", text)
        self.assertGreater(len(text), 250)

    def test_collect_does_not_modify_radar(self):
        with patch.object(fallback, "search", return_value=[
            {"id": "1", "title": "Ato", "date": "15/11/2026", "url": "https://www.in.gov.br/web/dou/-/ato"}
        ]):
            report = fallback.collect()
        self.assertEqual(report["status"], "consulta_concluida")
        self.assertEqual(report["results_count"], 1)
        self.assertFalse(report["radar_modified"])
        self.assertEqual(report["candidate_acts_created"], 0)

    def test_parse_results_fails_closed_if_markup_changes(self):
        with self.assertRaises(RuntimeError):
            fallback.parse_results(b"<html><body>maintenance</body></html>")


if __name__ == "__main__":
    unittest.main()
