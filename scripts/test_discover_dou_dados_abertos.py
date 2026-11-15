import unittest
from unittest.mock import patch
from scripts import discover_dou_dados_abertos as discovery


class DiscoveryTests(unittest.TestCase):
    def test_summarize_keeps_only_xml_or_archive_resources(self):
        item = {
            "id": "dataset-1", "name": "dou-2025", "title": "Diário Oficial da União",
            "resources": [
                {"id": "r1", "name": "edicao XML", "format": "XML", "url": "https://example.gov/a.xml"},
                {"id": "r2", "name": "planilha", "format": "CSV", "url": "https://example.gov/a.csv"},
            ],
        }
        result = discovery.summarize_dataset(item)
        self.assertEqual(result["id"], "dataset-1")
        self.assertEqual(len(result["xml_or_archive_resources"]), 1)
        self.assertEqual(result["xml_or_archive_resources"][0]["id"], "r1")

    @patch.object(discovery, "query_catalog")
    def test_discover_deduplicates_datasets(self, query):
        dataset = {"id": "same", "name": "dou", "title": "DOU", "resources": []}
        query.return_value = {"results": [dataset]}
        report = discovery.discover()
        self.assertEqual(report["datasets_count"], 1)
        self.assertEqual(report["status"], "catalogo_consultado")
        self.assertFalse(report["radar_modified"])
        self.assertEqual(report["candidate_acts_created"], 0)

    @patch.object(discovery, "query_catalog", side_effect=RuntimeError("indisponível"))
    def test_discover_reports_source_failure_without_raising(self, query):
        report = discovery.discover()
        self.assertEqual(report["status"], "fonte_indisponivel")
        self.assertEqual(len(report["errors"]), 2)
        self.assertFalse(report["radar_modified"])


if __name__ == "__main__":
    unittest.main()
