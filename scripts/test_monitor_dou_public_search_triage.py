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

    def test_full_text_can_reveal_academic_impact(self):
        item = {"title": "Portaria SERES", "abstract": "Dispõe sobre providências",
                "hierarchy": "Ministério da Educação/SERES",
                "full_text": "Determina a guarda do acervo acadêmico e dos registros acadêmicos.",
                "full_text_status": "obtido"}
        result = classify(item)
        self.assertEqual(result["triage"]["status"], "candidato_relevante")
        self.assertIn("acervo_e_documentacao", result["triage"]["impact_types"])
        self.assertTrue(result["triage"]["full_text_available"])

    def test_seres_act_without_specific_keyword_is_sent_to_human_review(self):
        item = {"title": "Ato administrativo", "abstract": "Providências institucionais",
                "hierarchy": "Ministério da Educação/Secretaria de Regulação e Supervisão da Educação Superior"}
        result = classify(item)
        self.assertEqual(result["triage"]["status"], "revisao_manual")
        self.assertTrue(result["triage"]["requires_human_validation"])

    def test_report_counts_impact_and_never_confirms_or_updates_radar(self):
        report = build_report({"status": "consulta_concluida",
            "education_authority_results": 1, "full_text_attempts": 1, "full_text_obtained": 1,
            "results": [{"title": "Portaria SERES", "abstract": "Providências",
                "hierarchy": "Ministério da Educação/SERES",
                "full_text": "Determina a guarda do acervo acadêmico.",
                "full_text_status": "obtido"}]})
        self.assertFalse(report["radar_modified"])
        self.assertEqual(report["acts_confirmed"], 0)
        self.assertEqual(report["counts"]["candidato_relevante"], 1)
        self.assertEqual(report["impact_counts"]["acervo_e_documentacao"], 1)
        self.assertEqual(report["full_text"]["obtained"], 1)


if __name__ == "__main__":
    unittest.main()