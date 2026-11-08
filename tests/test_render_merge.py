"""画面①の呼び方のまとめ：新しく加わった名指しの呼び方（merged_from）と、名前のつながり（chain）。"""
from anmoku.render import viewer_data


def cand(name, obs, aliases, score):
    return {"name": name, "category": "社内の人・役割", "observation_ids": obs, "aliases": aliases, "score": score,
            "evidence": [], "absence": 1, "absence_reason": "", "speakers": 1, "deps": 0, "waits": 0, "formula": "",
            "hub_hypothesis": False, "reason": ""}


def stage(k, cands):
    return {"k": k, "added": {"id": f"0{k}", "title": ""}, "candidates": cands}


def detect(stages):
    return {"stages": stages, "flow": {"title": "t", "lanes": []}, "interviews": [], "quotes": {"total": 0, "kept": 0}}


def merged(d):
    return [{c["name"]: c["merged_from"] for c in st["candidates"]} for st in viewer_data(d)["stages"]]


def test_new_named_alias_is_shown_at_the_stage_it_joins():
    d = detect([
        stage(1, [cand("課長", ["01#o1"], {"課長": 1}, 3)]),
        stage(2, [cand("佐藤課長", ["01#o1", "02#o1"], {"課長": 1, "佐藤さん": 2}, 10)]),
        stage(3, [cand("佐藤課長", ["01#o1", "02#o1", "03#o1"], {"課長": 1, "佐藤さん": 2, "統括": 3}, 20)]),
    ])
    m = merged(d)
    assert m[0]["課長"] == [] and m[1]["佐藤課長"] == ["佐藤さん"] and m[2]["佐藤課長"] == ["統括"]


def test_unnamed_observation_moving_in_is_not_a_merge():
    # 営業担当の候補にあった「明示なし」の観察が、次の段階でハブに移っても「まとめた」にしない
    d = detect([
        stage(1, [cand("佐藤課長", ["01#o1"], {"佐藤さん": 1}, 5), cand("営業担当", ["01#o2", "01#o3"], {"営業さん": 1, "明示なし": 1}, 0)]),
        stage(2, [cand("佐藤課長", ["01#o1", "01#o3", "02#o1"], {"佐藤さん": 2, "明示なし": 1}, 9),
                  cand("営業担当", ["01#o2"], {"営業さん": 1}, 0)]),
    ])
    m = merged(d)
    assert m[1]["佐藤課長"] == [] and m[1]["営業担当"] == []


def chains(d):
    return [{c["name"]: [(h["label"], h["count"]) for h in c["chain"]] for c in st["candidates"]}
            for st in viewer_data(d)["stages"]]


def test_chain_starts_with_the_speakers_word_and_counts_repeated_names():
    # 1人目は語り手の呼び方「課長」から始め、同じ名前が続いた段階は回数にまとめる
    d = detect([
        stage(1, [cand("課長（現場を回る人）", ["01#o1"], {"課長": 1}, 0)]),
        stage(2, [cand("工事統括の課長（佐藤さん）", ["01#o1", "02#o1"], {"課長": 1, "佐藤さん": 2}, 15)]),
        stage(3, [cand("工事統括の課長（佐藤さん）", ["01#o1", "02#o1", "03#o1"], {"課長": 1, "佐藤さん": 2, "統括": 3}, 25.5)]),
    ])
    ch = chains(d)
    assert ch[0]["課長（現場を回る人）"] == []
    assert ch[1]["工事統括の課長（佐藤さん）"] == [("課長", 1), ("工事統括の課長（佐藤さん）", 1)]
    assert ch[2]["工事統括の課長（佐藤さん）"] == [("課長", 1), ("工事統括の課長（佐藤さん）", 2)]


def test_no_chain_when_only_the_name_changes_without_merging():
    # 呼び方をまとめていない候補は、LLM が名前を言い換えただけなので鎖にしない
    d = detect([
        stage(1, [cand("エクセルの一覧（社内番号一覧表）", ["01#o2"], {"エクセルの一覧": 1}, 3)]),
        stage(2, [cand("社内番号のエクセル一覧", ["01#o2"], {"エクセルの一覧": 1}, 3)]),
    ])
    assert chains(d)[1]["社内番号のエクセル一覧"] == []


def test_chain_stays_after_merging_even_when_no_new_alias_joins():
    # 2人目でまとめた後、3人目で新しい呼び方が加わらなくても、鎖は残り回数が増える
    d = detect([
        stage(1, [cand("課長（現場を回る人）", ["01#o1"], {"課長": 1}, 0)]),
        stage(2, [cand("工事統括の課長（佐藤さん）", ["01#o1", "02#o1"], {"課長": 1, "佐藤さん": 2}, 15)]),
        stage(3, [cand("工事統括の課長（佐藤さん）", ["01#o1", "02#o1"], {"課長": 1, "佐藤さん": 2}, 15)]),
    ])
    ch = chains(d)
    assert ch[2]["工事統括の課長（佐藤さん）"] == [("課長", 1), ("工事統括の課長（佐藤さん）", 2)]


def test_no_chain_when_merged_but_the_name_stays_the_same():
    # 呼び方をまとめても、名前が最初の呼び方のまま変わらなければ、鎖は1つだけなので出さない
    d = detect([
        stage(1, [cand("課長", ["01#o1"], {"課長": 1}, 3)]),
        stage(2, [cand("課長", ["01#o1", "02#o1"], {"課長": 1, "佐藤さん": 1}, 9)]),
    ])
    assert merged(d)[1]["課長"] == ["佐藤さん"]
    assert chains(d)[1]["課長"] == []


def test_first_link_joins_several_speaker_words_and_falls_back_to_the_name():
    # 最初の段階で語り手の呼び方が2つあれば「・」でつなぐ。名指しが無ければ候補の名前を使う。
    # 2人目で初めて出た候補も、その段階を鎖の始まりにする
    d = detect([
        stage(1, [cand("課長", ["01#o1"], {"課長": 1, "統括": 1}, 3),
                  cand("職人の手配の担当", ["01#o2"], {"明示なし": 1}, 1)]),
        stage(2, [cand("工事統括の課長", ["01#o1", "02#o1"], {"課長": 1, "統括": 1, "佐藤さん": 1}, 9),
                  cand("職人の手配の担当", ["01#o2", "02#o2"], {"明示なし": 1, "手配さん": 1}, 2),
                  cand("経理", ["02#o3"], {"経理さん": 1}, 1)]),
        stage(3, [cand("工事統括の課長", ["01#o1", "02#o1"], {"課長": 1, "統括": 1, "佐藤さん": 1}, 9),
                  cand("職人の手配の担当", ["01#o2", "02#o2"], {"明示なし": 1, "手配さん": 1}, 2),
                  cand("経理の山本課長", ["02#o3", "03#o3"], {"経理さん": 1, "山本課長": 1}, 3)]),
    ])
    ch = chains(d)
    assert ch[1]["工事統括の課長"] == [("課長・統括", 1), ("工事統括の課長", 1)]
    # 名指しが無かった最初の段階は候補の名前になり、2段階目も同じ名前なので鎖は1つ（出さない）
    assert ch[1]["職人の手配の担当"] == []
    assert ch[2]["経理の山本課長"] == [("経理さん", 1), ("経理の山本課長", 1)]


def test_viewer_template_drops_the_old_merge_label_and_stage_count():
    # 画面の文字の受入条件（見出し・「まとめた」の削除・括弧の補足）を、テンプレートの中身で確かめる
    from pathlib import Path
    import anmoku.render as r
    tpl = (Path(r.__file__).parent / "viewer_template.html").read_text(encoding="utf-8")
    assert "をまとめた" not in tpl and "人目まで" not in tpl
    # 見出し：語り手の名前を「・」でつなぐ。取れない記録があれば人数だけ。0人目はそのまま
    assert 'textContent = names.every(Boolean) ? `${names.join("・")}の記録から` : `${k}人分の記録から`;' in tpl
    assert '$("stageTitle").textContent = "0人目";' in tpl
    # 名前の行：つながりを「→」で並べ、2回以上続いた名前に「×N」を付ける
    assert 'c.chain.map(h => `${nameHtml(h.label)}${h.count > 1 ? `<span class="times">×${h.count}</span>` : ""}`).join(`<span class="arrow">→</span>`)' in tpl
    # 括弧の補足は小さい灰色（.note）。根拠パネルの見出しも同じ関数を通す
    assert '<span class="note">（${esc(m[2])}）</span>' in tpl and "<h3>${nameHtml(c.name)}</h3>" in tpl


def test_brand_new_candidate_with_two_aliases_is_not_shown_as_merge():
    d = detect([stage(1, [cand("課長", ["01#o1"], {"課長": 1}, 3)]),
                stage(2, [cand("課長", ["01#o1"], {"課長": 1}, 3), cand("佐藤", ["02#o1", "02#o2"], {"佐藤さん": 1, "統括": 1}, 5)])])
    assert merged(d)[1]["佐藤"] == []
