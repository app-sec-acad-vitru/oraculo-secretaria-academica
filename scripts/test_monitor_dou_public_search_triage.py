import unittest
from scripts.monitor_dou_public_search_triage import classify, build_report


class PublicSearchTriageTests(unittest.TestCase):
    def test_mec_act_with_academic_subject_is_relevant_candidate(self):
        item = {"title": "Portaria MEC", "abstract": "Reconhecimento de curso superior",
                "hierarchy": "Ministério da Educação/SERES"}
        result = classify(item)
        self.assertEqual(result["triage"]["status"], "candidato_relevante")
        self.assertFalse(result["triage"]["confirmed"])

    def test_non_education_authority_is_out_of_scope_even_with_word_course(self):
        item = {"title": "Retificação", "abstract": "curso e autorização de agente",
                "hierarchy": "Ministério de Minas e Energia/Agência Nacional do Petróleo"}
        result = classify(item)
        self.assertEqual(result["triage"]["status"], "fora_do_escopo")

    def test_academic_subject_without_confirmed_authority_goes_to_manual_review(self):
        item = {"title": "Ato sobre diploma digital", "abstract": "educação superior",
                "hierarchy": "Órgão não identificado"}
        result = classify(item)
        self.assertEqual(result["triage"]["status"], "revisao_manual")

    def test_report_never_changes_radar_or_confirms_acts(self):
        report = build_report({"status": "consulta_concluida", "results": [
            {"title": "Portaria MEC", "abstract": "Reconhecimento de curso superior",
             "hierarchy": "Ministério da Educação/SERES"}
        ]})
        self.assertFalse(report["radar_modified"])
        self.assertEqual(report["acts_confirmed"], 0)
        self.assertEqual(report["counts"]["candidato_relevante"], 1)


if __name__ == "__main__":
    unittest.main()
