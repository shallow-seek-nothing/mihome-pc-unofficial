"""Home sharing members: joining the two lists the gateway returns."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mihome_pc.domain import member  # noqa: E402
from mihome_pc.web import server as web_server  # noqa: E402

PAYLOAD = {
    "list": [
        {"uid": 1, "permit_level": 10, "inviter": 1, "invite_nick": "me",
         "invite_time": 1559060390, "status": 1, "note": ""},
        {"uid": 2, "permit_level": 10, "inviter": 1, "invite_nick": "x",
         "invite_time": 1600000000, "status": 1, "note": ""},
        {"uid": 3, "permit_level": 0, "inviter": 1, "invite_nick": "guest",
         "invite_time": 0, "status": 1, "note": "\u5ba2\u4eba"},
    ],
    "userdetail": [
        {"uid": 1, "nick_name": "\u4e3b\u4eba", "icon": "https://x/1.png"},
        {"uid": 2, "nick_name": "\u5bb6\u4eba", "icon": ""},
    ],
}


class TestNormalise(unittest.TestCase):
    def setUp(self):
        self.data = member.normalise(PAYLOAD, owner_uid=1)

    def test_one_row_per_member(self):
        self.assertEqual(self.data["count"], 3)
        self.assertEqual([item["uid"] for item in self.data["members"]],
                         [1, 2, 3])

    def test_nickname_comes_from_userdetail(self):
        first = self.data["members"][0]
        self.assertEqual(first["name"], "\u4e3b\u4eba")
        self.assertEqual(first["icon"], "https://x/1.png")

    def test_roles(self):
        roles = {item["uid"]: item["role"] for item in self.data["members"]}
        self.assertEqual(roles, {1: "owner", 2: "admin", 3: "member"})
        self.assertEqual(self.data["members"][2]["role_label"], "\u6210\u5458")

    def test_missing_detail_falls_back_to_invite_nick(self):
        third = self.data["members"][2]
        self.assertEqual(third["name"], "guest")
        self.assertEqual(third["note"], "\u5ba2\u4eba")

    def test_empty_payload(self):
        self.assertEqual(member.normalise(None), {"members": [], "count": 0})


class StubClient:
    base_url = "https://stub.invalid"

    def __init__(self, fail=False):
        self.fail = fail
        self.calls = 0

    def homes(self):
        return {"homelist": [{"id": "h1", "name": "\u5bb6", "uid": 1}]}

    def home_members(self, home_id):
        self.calls += 1
        self.home_id = home_id
        if self.fail:
            raise RuntimeError("boom")
        return PAYLOAD


class TestProviderMembers(unittest.TestCase):
    def test_members_use_the_owner_from_the_home(self):
        client = StubClient()
        provider = web_server.CloudProvider(None, client=client)
        data = provider.members()
        self.assertEqual(data["count"], 3)
        self.assertEqual([item["role"] for item in data["members"]],
                         ["owner", "admin", "member"])
        self.assertEqual(client.home_id, "h1")

    def test_failure_degrades_to_empty(self):
        provider = web_server.CloudProvider(None, client=StubClient(fail=True))
        self.assertEqual(provider.members(), {"members": [], "count": 0})


if __name__ == "__main__":
    unittest.main()
