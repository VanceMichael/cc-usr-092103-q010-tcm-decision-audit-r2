"""机构层级：总院—院区、区域医疗中心—站点、医联体—成员。

版本登记可以直接落在某机构上，也可以沿 parent_id 向上继承
（站点继承区域医疗中心；院区继承总院）。医联体授权不沿行政
父子链自动传递，必须单独登记。
"""

from .repository import Repository


class Organizations:
    def __init__(self, repo: Repository):
        self.orgs = repo.organizations

    def get(self, org_id: str) -> dict:
        if org_id not in self.orgs:
            raise KeyError(f"未知机构 {org_id}")
        return self.orgs[org_id]

    def chain(self, org_id: str) -> list[str]:
        """从机构自身沿 parent_id 到顶的有序链。"""
        chain: list[str] = []
        current = org_id
        seen: set[str] = set()
        while current is not None:
            if current in seen:
                raise ValueError(f"机构层级存在环：{org_id}")
            seen.add(current)
            chain.append(current)
            current = self.orgs[current]["parent_id"]
        return chain

    def resolve_registration(self, org_id: str, model_uid: str, registrations) -> dict | None:
        """沿层级链找到该模型最近的一条版本登记。"""
        for candidate in self.chain(org_id):
            reg = registrations.get((candidate, model_uid))
            if reg is not None:
                return reg
        return None

    def are_in_alliance(self, org_a: str, org_b: str) -> bool:
        """两机构是否同属一个医联体（任一祖先为 alliance 类型）。"""
        def alliances(org_id: str) -> set[str]:
            return {oid for oid in self.chain(org_id)
                    if self.orgs[oid]["org_type"] == "alliance"}
        return bool(alliances(org_a) & alliances(org_b))

    def label(self, org_id: str) -> str:
        org = self.get(org_id)
        return f'{org["name"]}（{org_id}）'
