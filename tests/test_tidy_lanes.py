"""画面②のレーンの整え方：ハブの呼び方の行は1レーンに、今あるレーン名＋（仮）は今あるレーンに。"""
from anmoku.render import tidy_lanes

BEFORE = [{"lane": "営業", "owner": "営業担当", "task": "見積作成"},
          {"lane": "現場", "owner": "現場監督", "task": "施工管理"},
          {"lane": "経理", "owner": "経理担当（山本課長）", "task": "請求"}]
HUB = [{"name": "工事統括の課長（佐藤さん）", "aliases": ["課長", "佐藤さん", "統括"]}]


def row(lane, owner, task, p=True):
    return {"lane": lane, "owner": owner, "task": task, "provisional": p}


def lanes(after):
    return [r["lane"] for r in tidy_lanes(after, BEFORE, HUB)]


def test_hub_aliases_go_to_one_hub_lane():
    got = lanes([row("統括（仮）", "工事統括（仮）", "予備費の相談（仮）"),
                 row("課長（仮）", "工事統括の課長（佐藤さん）（仮）", "値引きの事前相談（仮）"),
                 row("佐藤さん（仮）", "佐藤さん（仮）", "職人の割り振り（仮）")])
    assert got == ["工事統括の課長（佐藤さん）（仮）"] * 3


def test_existing_lane_with_provisional_mark_goes_to_existing_lane_and_keeps_mark():
    r = tidy_lanes([row("現場（仮）", "現場監督（仮）", "小さい追加は現場で決める（仮）")], BEFORE, HUB)[0]
    assert r["lane"] == "現場" and r["provisional"]


def test_official_rows_are_unchanged_and_yamamoto_is_not_mixed_into_hub():
    after = BEFORE + [row("経理（仮）", "経理担当（山本課長）（仮）", "追加工事の粗利の確認（仮）"),
                      row("山本課長（仮）", "経理担当（山本課長）（仮）", "粗利の確認（仮）")]
    got = tidy_lanes(after, BEFORE, HUB)
    assert got[:3] == BEFORE
    assert got[3]["lane"] == "経理"
    assert got[4]["lane"] == "山本課長（仮）"  # 「課長」は役職名だけなので、山本課長には当てない
    assert all("佐藤" not in r["lane"] for r in got if "山本" in r["owner"])
