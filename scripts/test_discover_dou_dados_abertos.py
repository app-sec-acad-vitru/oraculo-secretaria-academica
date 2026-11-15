import unittest
from unittest.mock import patch
from scripts import discover_dou_dados_abertos as discovery


class DiscoveryTests(unittest.TestCase):
    def test_missing_token_reports_authentication_required_without_network(self):
        with patch.object(discovery, "query_catalog") as query:
            report = discovery.discover(token="")
        query.assert_not_called()
        self.assertEqual(report["status"], "autenticacao_necessaria")
        self.assertFalse(report["radar_modified"])
        self.assertEqual(report["candidate_acts_created"], 0)

    def test_summarize_keeps_only_xml_or_archive_resources(self):
        item = {
            "id": "dataset-1", "nome": "dou-2025", "titulo": "Diário Oficial da União",
            "recursos": [
                {"id": "r1", "titulo": "edição XML", "formato": "XML", "link": "https://example.gov/a.xml"},
                {"id": "r2", "titulo": "planilha", "formato": "CSV", "link": "https://example.gov/a.csv"},
            ],
        }
        result = discovery.summarize_dataset(item)
        self.assertEqual(result["id"], "dataset-1")
        self.assertEqual(len(result["xml_or_archive_resources"]), 1)
        self.assertEqual(result["xml_or_archive_resources"][0]["id"], "r1")

    @patch.object(discovery, "query_catalog")
    def test_discover_deduplicates_datasets(self, query):
        dataset = {"id": "same", "nome": "dou", "titulo": "DOU", "recursos": []}
        query.return_value = [dataset]
        report = discovery.discover(token="test-token")
        self.assertEqual(report["datasets_count"], 1)
        self.assertEqual(report["status"], "catalogo_consultado")
        self.assertFalse(report["radar_modified"])
        self.assertEqual(report["candidate_acts_created"], 0)

    @patch.object(discovery, "query_catalog", side_effect=__import__("urllib.error").error.HTTPError(
        "https://dados.gov.br", 401, "Unauthorized", {}, None
    ))
    def test_discover_reports_auth_rejected(self, query):
        report = discovery.discover(token="test-token")
        self.assertEqual(report["status"], "autenticacao_rejeitada")
        self.assertEqual(len(report["errors"]), 2)
        self.assertFalse(report["radar_modified"])


if __name__ == "__main__":
    unittest.main()
