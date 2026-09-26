"""方证术语：病、证、治法、方剂、症舌脉与名医经验来源。"""

from .repository import Repository


class Terminology:
    def __init__(self, repo: Repository):
        self.repo = repo
        self.diseases = repo.index_terms("diseases")
        self.syndromes = repo.index_terms("syndromes")
        self.principles = repo.index_terms("treatment_principles")
        self.formulas = repo.index_terms("formulas")
        self.manifestations = repo.index_terms("manifestations")
        self.experience = repo.index_terms("master_experience_sources")

    def version(self) -> str:
        return self.repo.terms["schema"]

    def check_request(self, disease_code: str, syndrome_code: str,
                      manifestation_codes: list[str]) -> list[str]:
        """返回请求中无法在当前术语表中解析的编码（空列表=全部可解析）。"""
        unknown: list[str] = []
        if disease_code not in self.diseases:
            unknown.append(disease_code)
        syn = self.syndromes.get(syndrome_code)
        if syn is None:
            unknown.append(syndrome_code)
        elif disease_code not in syn["disease_codes"]:
            unknown.append(f"{syndrome_code}!:{disease_code}")
        for code in manifestation_codes:
            if code not in self.manifestations:
                unknown.append(code)
        return unknown

    def experience_for(self, source_codes: list[str], disease_code: str,
                       syndrome_code: str) -> list[dict]:
        """返回版本经验来源中与本病、本证匹配的条目。"""
        hits = []
        for code in source_codes:
            src = self.experience.get(code)
            if src and disease_code in src["disease_codes"] and syndrome_code in src["syndrome_codes"]:
                hits.append(src)
        return hits
