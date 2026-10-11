import { describe, expect, it, vi } from "vitest";
import { mountDecisionPanel } from "./decision-panel";
import { rest } from "../api/rest";

vi.mock("../api/rest", () => ({ rest: { metadata: vi.fn() } }));

describe("mountDecisionPanel", () => {
  it("summarizes the meeting and lists confirmed conclusions on the first screen", async () => {
    document.body.innerHTML = `
      <div id="meeting-summary"></div>
      <div id="confirmed-count"></div><div id="confirmed-list"></div>
      <div id="focus-count"></div><div id="focus-list"></div>
      <div id="conflict-count"></div><div id="conflict-list"></div>
      <div id="action-count"></div><div id="action-list"></div>
      <section id="utterance-rail"><span id="utterance-count"></span><div id="utterance-list"></div></section>`;
    const panel = mountDecisionPanel(() => undefined);
    await panel.update([
      { id: "root", data: { type: "issue", label: "产品路线评审", parent_id: null } },
      { id: "decision", data: { type: "conclusion", label: "先做封闭试点", status: "confirmed", manual_override: { status: true }, parent_id: "root" } },
      { id: "todo", data: { type: "action", label: "准备试点清单", status: "pending", parent_id: "root" } },
    ]);

    expect(document.getElementById("meeting-summary")?.textContent).toContain("产品路线评审");
    expect(document.getElementById("meeting-summary")?.textContent).toContain("下一步 1");
    expect(document.getElementById("confirmed-count")?.textContent).toBe("1");
    expect(document.getElementById("confirmed-list")?.textContent).toContain("先做封闭试点");
  });

  it("shows a pending decision with both sides and opens human review", async () => {
    document.body.innerHTML = `
      <div id="meeting-summary"></div><div id="decision-card-list"></div>
      <div id="confirmed-count"></div><div id="confirmed-list"></div>
      <div id="focus-count"></div><div id="focus-list"></div>
      <div id="conflict-count"></div><div id="conflict-list"></div>
      <div id="action-count"></div><div id="action-list"></div>
      <section id="utterance-rail"><span id="utterance-count"></span><div id="utterance-list"></div></section>`;
    const review = vi.fn();
    const panel = mountDecisionPanel(() => undefined, review);
    await panel.update([
      { id: "decision", data: { type: "conclusion", label: "先试点", status: "proposed" } },
      { id: "for", data: { type: "point", label: "投入较低" } },
      { id: "against", data: { type: "point", label: "隐私风险" } },
      { id: "support", shape: "edge", source: "for", target: "decision", data: { relation: "support" } },
      { id: "oppose", shape: "edge", source: "against", target: "decision", data: { relation: "oppose" } },
    ]);
    const list = document.getElementById("decision-card-list")!;
    expect(list.textContent).toContain("待人工确认");
    expect(list.textContent).toContain("投入较低");
    expect(list.textContent).toContain("隐私风险");
    list.querySelector<HTMLButtonElement>(".decision-topic-review")!.click();
    expect(review).toHaveBeenCalledWith("decision");
    list.querySelector<HTMLButtonElement>(".decision-review")!.click();
    expect(review).toHaveBeenCalledWith("decision");
    expect(review).toHaveBeenCalledTimes(2);
  });

  it("shows source utterances and speakers directly on the decision card", async () => {
    document.body.innerHTML = `
      <div id="meeting-summary"></div><div id="decision-card-list"></div>
      <div id="confirmed-count"></div><div id="confirmed-list"></div>
      <div id="focus-count"></div><div id="focus-list"></div>
      <div id="conflict-count"></div><div id="conflict-list"></div>
      <div id="action-count"></div><div id="action-list"></div>
      <section id="utterance-rail"><span id="utterance-count"></span><div id="utterance-list"></div></section>`;
    vi.mocked(rest.metadata).mockResolvedValue({ records: [
      { meta_id: "u_decision", kind: "utt", text: "先做小范围试点。", display_name: "主持人" },
      { meta_id: "u_against", kind: "utt", text: "隐私数据的边界仍需解决。", display_name: "法务" },
    ] });
    const focus = vi.fn();
    const panel = mountDecisionPanel(focus);
    await panel.update([
      { id: "decision", data: { type: "conclusion", label: "先试点", status: "proposed", metadata_refs: ["u_decision"] } },
      { id: "against", data: { type: "point", label: "隐私风险", metadata_refs: ["u_against"] } },
      { id: "oppose", shape: "edge", source: "against", target: "decision", data: { relation: "oppose" } },
    ]);

    const list = document.getElementById("decision-card-list")!;
    expect(list.textContent).toContain("主持人：“先做小范围试点。"
    );
    expect(list.textContent).toContain("法务：“隐私数据的边界仍需解决。"
    );
    expect(list.textContent).toContain("存在反对依据");
    list.querySelector<HTMLButtonElement>(".decision-quote-oppose")!.click();
    expect(focus).toHaveBeenCalledWith("against");
  });

  it("lets the reviewer choose which duplicate node to keep", async () => {
    document.body.innerHTML = `
      <div id="meeting-summary"></div><div id="decision-card-list"></div>
      <div id="confirmed-count"></div><div id="confirmed-list"></div>
      <div id="focus-count"></div><div id="focus-list"></div>
      <div id="conflict-count"></div><div id="conflict-list"></div>
      <div id="action-count"></div><div id="action-list"></div>
      <section id="utterance-rail"><span id="utterance-count"></span><div id="utterance-list"></div></section>`;
    const merge = vi.fn();
    vi.mocked(rest.metadata).mockResolvedValue({ records: [
      { meta_id: "dup_u1", kind: "utt", text: "每周五复盘远程试点并汇总故障", display_name: "甲" },
      { meta_id: "dup_u2", kind: "utt", text: "周五复盘远程试点并汇总故障", display_name: "乙" },
    ] });
    const panel = mountDecisionPanel(() => undefined, () => undefined, merge);
    await panel.update([
      { id: "left", data: { type: "point", label: "复盘远程试点", metadata_refs: ["dup_u1"] } },
      { id: "right", data: { type: "point", label: "评估试点表现", metadata_refs: ["dup_u2"] } },
    ]);

    const list = document.getElementById("decision-card-list")!;
    expect(list.textContent).toContain("可能重复");
    expect(list.textContent).toContain("原话相似");
    expect(list.textContent).toContain("词项重合分");
    expect(list.textContent).toContain("甲：“每周五复盘远程试点并汇总故障”");
    expect(list.textContent).toContain("乙：“周五复盘远程试点并汇总故障”");
    list.querySelector<HTMLButtonElement>(".duplicate-keep-left")!.click();
    expect(merge).toHaveBeenCalledWith("right", "left");
  });

  it("persists a dismissed candidate per meeting and lets the reviewer restore it", async () => {
    const storageKey = "huimai:decision-hints-dismissed:mtg_candidate_dismissal";
    localStorage.removeItem(storageKey);
    document.body.innerHTML = `
      <div id="meeting-summary"></div><div id="decision-card-list"></div>
      <div id="confirmed-count"></div><div id="confirmed-list"></div>
      <div id="focus-count"></div><div id="focus-list"></div>
      <div id="conflict-count"></div><div id="conflict-list"></div>
      <div id="action-count"></div><div id="action-list"></div>
      <section id="utterance-rail"><span id="utterance-count"></span><div id="utterance-list"></div></section>`;
    vi.mocked(rest.metadata).mockResolvedValue({ records: [
      { meta_id: "dismiss_u1", kind: "utt", text: "每周五复盘远程试点并汇总故障", display_name: "甲" },
      { meta_id: "dismiss_u2", kind: "utt", text: "周五复盘远程试点并汇总故障", display_name: "乙" },
    ] });
    const cells = [
      { id: "dismiss_left", data: { type: "point", label: "复盘远程试点", metadata_refs: ["dismiss_u1"] } },
      { id: "dismiss_right", data: { type: "point", label: "评估试点表现", metadata_refs: ["dismiss_u2"] } },
    ];
    const panel = mountDecisionPanel(() => undefined, undefined, undefined, undefined, undefined, "mtg_candidate_dismissal");
    await panel.update(cells);
    document.querySelector<HTMLButtonElement>(".candidate-dismiss")!.click();
    expect(document.querySelector(".possible-duplicate-row")).toBeNull();

    document.body.innerHTML = `
      <div id="meeting-summary"></div><div id="decision-card-list"></div>
      <div id="confirmed-count"></div><div id="confirmed-list"></div>
      <div id="focus-count"></div><div id="focus-list"></div>
      <div id="conflict-count"></div><div id="conflict-list"></div>
      <div id="action-count"></div><div id="action-list"></div>
      <section id="utterance-rail"><span id="utterance-count"></span><div id="utterance-list"></div></section>`;
    const refreshed = mountDecisionPanel(() => undefined, undefined, undefined, undefined, undefined, "mtg_candidate_dismissal");
    await refreshed.update(cells);
    expect(document.querySelector(".possible-duplicate-row")).toBeNull();
    document.querySelector<HTMLButtonElement>(".candidate-restore")!.click();
    expect(document.querySelector(".possible-duplicate-row")).not.toBeNull();
    localStorage.removeItem(storageKey);
  });

  it("lets the reviewer record a same-topic opposition candidate", async () => {
    document.body.innerHTML = `
      <div id="meeting-summary"></div><div id="decision-card-list"></div>
      <div id="confirmed-count"></div><div id="confirmed-list"></div>
      <div id="focus-count"></div><div id="focus-list"></div>
      <div id="conflict-count"></div><div id="conflict-list"></div>
      <div id="action-count"></div><div id="action-list"></div>
      <section id="utterance-rail"><span id="utterance-count"></span><div id="utterance-list"></div></section>`;
    const link = vi.fn();
    vi.mocked(rest.metadata).mockResolvedValue({ records: [
      { meta_id: "u_for", kind: "utt", text: "提高远程办公比例能提高协作效率。", display_name: "甲" },
      { meta_id: "u_against", kind: "utt", text: "不提高远程办公比例，避免降低协作效率。", display_name: "乙" },
    ] });
    const panel = mountDecisionPanel(() => undefined, () => undefined, undefined, link);
    await panel.update([
      { id: "root", data: { type: "issue", label: "产品评审", parent_id: null } },
      { id: "topic", data: { type: "issue", label: "远程办公", parent_id: "root" } },
      { id: "for", data: { type: "point", label: "远程办公提升协作效率", parent_id: "topic", metadata_refs: ["u_for"] } },
      { id: "against", data: { type: "point", label: "远程办公不提升协作效率", parent_id: "topic", metadata_refs: ["u_against"] } },
    ]);

    const list = document.getElementById("decision-card-list")!;
    expect(list.textContent).toContain("可能存在矛盾");
    expect(list.textContent).toContain("词项重合分");
    expect(list.textContent).toContain("甲：“提高远程办公比例能提高协作效率。");
    expect(list.textContent).toContain("乙：“不提高远程办公比例，避免降低协作效率。");
    list.querySelector<HTMLButtonElement>(".opposition-confirm")!.click();
    expect(link).toHaveBeenCalledWith("for", "against");
  });

  it("uses linked source quotes to expose opposing positions with generic labels", async () => {
    document.body.innerHTML = `
      <div id="meeting-summary"></div><div id="decision-card-list"></div>
      <div id="confirmed-count"></div><div id="confirmed-list"></div>
      <div id="focus-count"></div><div id="focus-list"></div>
      <div id="conflict-count"></div><div id="conflict-list"></div>
      <div id="action-count"></div><div id="action-list"></div>
      <section id="utterance-rail"><span id="utterance-count"></span><div id="utterance-list"></div></section>`;
    vi.mocked(rest.metadata).mockResolvedValue({ records: [
      { meta_id: "u_for", kind: "utt", text: "建议提高远程办公比例，以提升团队协作效率。", display_name: "甲" },
      { meta_id: "u_against", kind: "utt", text: "不建议提高远程办公比例，会降低团队协作效率。", display_name: "乙" },
    ] });
    const panel = mountDecisionPanel(() => undefined);
    await panel.update([
      { id: "root", data: { type: "issue", label: "产品评审", parent_id: null } },
      { id: "topic", data: { type: "issue", label: "远程办公", parent_id: "root" } },
      { id: "for", data: { type: "point", label: "方案倾向", parent_id: "topic", metadata_refs: ["u_for"] } },
      { id: "against", data: { type: "point", label: "方案评估", parent_id: "topic", metadata_refs: ["u_against"] } },
    ]);

    const list = document.getElementById("decision-card-list")!;
    expect(list.textContent).toContain("原话重合");
    expect(list.textContent).toContain("甲：“建议提高远程办公比例");
    expect(list.textContent).toContain("乙：“不建议提高远程办公比例");
  });

  it("explains when an opposition hint comes from an explicit rebuttal in the source", async () => {
    document.body.innerHTML = `
      <div id="meeting-summary"></div><div id="decision-card-list"></div>
      <div id="confirmed-count"></div><div id="confirmed-list"></div>
      <div id="focus-count"></div><div id="focus-list"></div>
      <div id="conflict-count"></div><div id="conflict-list"></div>
      <div id="action-count"></div><div id="action-list"></div>
      <section id="utterance-rail"><span id="utterance-count"></span><div id="utterance-list"></div></section>`;
    vi.mocked(rest.metadata).mockResolvedValue({ records: [
      { meta_id: "u_evidence", kind: "utt", text: "斯坦福研究显示远程员工绩效提升百分之十三。", display_name: "甲" },
      { meta_id: "u_rebuttal", kind: "utt", text: "那份研究样本是呼叫中心，推演到所有岗位是以偏概全。", display_name: "乙" },
    ] });
    const panel = mountDecisionPanel(() => undefined);
    await panel.update([
      { id: "root", data: { type: "issue", label: "会议", parent_id: null } },
      { id: "topic", data: { type: "issue", label: "远程办公", parent_id: "root" } },
      { id: "evidence", data: { type: "evidence", label: "绩效提升数据", parent_id: "topic", metadata_refs: ["u_evidence"] } },
      { id: "rebuttal", data: { type: "point", label: "样本范围质疑", parent_id: "topic", metadata_refs: ["u_rebuttal"] } },
    ]);

    const list = document.getElementById("decision-card-list")!;
    expect(list.textContent).toContain("原话含明确反驳表达");
    expect(list.textContent).toContain("命中：研究");
    expect(list.textContent).not.toContain("原话含明确反驳表达 5%");
  });

  it("labels semantic debate cues without presenting their internal rank as confidence", async () => {
    document.body.innerHTML = `
      <div id="meeting-summary"></div><div id="decision-card-list"></div>
      <div id="confirmed-count"></div><div id="confirmed-list"></div>
      <div id="focus-count"></div><div id="focus-list"></div>
      <div id="conflict-count"></div><div id="conflict-list"></div>
      <div id="action-count"></div><div id="action-list"></div>
      <section id="utterance-rail"><span id="utterance-count"></span><div id="utterance-list"></div></section>`;
    vi.mocked(rest.metadata).mockResolvedValue({ records: [
      { meta_id: "u_remote", kind: "utt", text: "我方认为远程办公是效率与人本管理的双重胜利。", display_name: "甲" },
      { meta_id: "u_office", kind: "utt", text: "我方认为固定坐班是协作效率与执行力的根基，远程只是权宜之计。", display_name: "乙" },
    ] });
    const panel = mountDecisionPanel(() => undefined);
    await panel.update([
      { id: "n_issue_root", data: { type: "issue", label: "会议", parent_id: null } },
      { id: "remote", data: { type: "point", label: "远程办公主张", parent_id: "n_issue_root", metadata_refs: ["u_remote"] } },
      { id: "office", data: { type: "point", label: "固定坐班主张", parent_id: "n_issue_root", metadata_refs: ["u_office"] } },
    ]);

    const list = document.getElementById("decision-card-list")!;
    expect(list.textContent).toContain("相反办公立场线索");
    expect(list.textContent).not.toContain("词项重合分 56/100");
  });

  it("shows the two topic assignments on a cross-topic rebuttal hint", async () => {
    document.body.innerHTML = `
      <div id="meeting-summary"></div><div id="decision-card-list"></div>
      <div id="confirmed-count"></div><div id="confirmed-list"></div>
      <div id="focus-count"></div><div id="focus-list"></div>
      <div id="conflict-count"></div><div id="conflict-list"></div>
      <div id="action-count"></div><div id="action-list"></div>
      <section id="utterance-rail"><span id="utterance-count"></span><div id="utterance-list"></div></section>`;
    vi.mocked(rest.metadata).mockResolvedValue({ records: [
      { meta_id: "u_old", kind: "utt", text: "关键决策常发生在茶水间和工位旁的非正式交流里，远程丢掉了这些瞬间。" },
      { meta_id: "u_reply", kind: "utt", text: "对方说非正式交流重要，可线上同样有虚拟茶水间和群聊闲聊，只是形式变了。" },
    ] });
    const panel = mountDecisionPanel(() => undefined);
    await panel.update([
      { id: "n_issue_root", data: { type: "issue", label: "会议", parent_id: null } },
      { id: "office", data: { type: "issue", label: "固定坐班", parent_id: "n_issue_root" } },
      { id: "remote", data: { type: "issue", label: "远程办公", parent_id: "n_issue_root" } },
      { id: "old-claim", data: { type: "point", label: "非正式交流影响决策", parent_id: "office", metadata_refs: ["u_old"] } },
      { id: "reply", data: { type: "evidence", label: "线上虚拟茶水间", parent_id: "remote", metadata_refs: ["u_reply"] } },
    ]);

    const list = document.getElementById("decision-card-list")!;
    expect(list.textContent).toContain("议题归属不同：固定坐班 ↔ 远程办公");
    expect(list.textContent).toContain("请核对归属和发言后再确认");
    expect(list.querySelector<HTMLButtonElement>(".opposition-confirm")).not.toBeNull();
  });

  it("opens manual editing from a suggested issue assignment", async () => {
    document.body.innerHTML = `
      <div id="meeting-summary"></div><div id="decision-card-list"></div>
      <div id="confirmed-count"></div><div id="confirmed-list"></div>
      <div id="focus-count"></div><div id="focus-list"></div>
      <div id="conflict-count"></div><div id="conflict-list"></div>
      <div id="action-count"></div><div id="action-list"></div>
      <section id="utterance-rail"><span id="utterance-count"></span><div id="utterance-list"></div></section>`;
    const assign = vi.fn();
    const panel = mountDecisionPanel(() => undefined, () => undefined, undefined, undefined, assign);
    await panel.update([
      { id: "root", data: { type: "issue", label: "会议", parent_id: null } },
      { id: "customer", data: { type: "issue", label: "客户回访与服务质量", parent_id: "root" } },
      { id: "followup", data: { type: "point", label: "回访需要形成问题闭环", parent_id: "customer" } },
      { id: "mobile", data: { type: "issue", label: "移动端版本发布", parent_id: "root" } },
      { id: "decision", data: { type: "conclusion", label: "客户回访增加问题闭环", parent_id: "root" } },
    ]);

    const button = document.querySelector<HTMLButtonElement>(".topic-assignment-suggestion");
    expect(button?.textContent).toContain("客户回访与服务质量");
    button!.click();
    expect(assign).toHaveBeenCalledWith("decision", "customer");
  });

  it("lets the reviewer hide and restore an incorrect issue assignment suggestion", async () => {
    const storageKey = "huimai:decision-hints-dismissed:mtg_topic_dismissal";
    localStorage.removeItem(storageKey);
    document.body.innerHTML = `
      <div id="meeting-summary"></div><div id="decision-card-list"></div>
      <div id="confirmed-count"></div><div id="confirmed-list"></div>
      <div id="focus-count"></div><div id="focus-list"></div>
      <div id="conflict-count"></div><div id="conflict-list"></div>
      <div id="action-count"></div><div id="action-list"></div>
      <section id="utterance-rail"><span id="utterance-count"></span><div id="utterance-list"></div></section>`;
    vi.mocked(rest.metadata).mockResolvedValue({ records: [
      { meta_id: "topic_u_remote", kind: "utt", text: "远程办公能减少通勤时间，改善员工日常效率。" },
      { meta_id: "topic_u_office", kind: "utt", text: "固定坐班便于面对面协作和现场沟通。" },
      { meta_id: "topic_u_decision", kind: "utt", text: "远程办公减少通勤时间，让员工能更高效地安排工作。" },
    ] });
    const cells = [
      { id: "root", data: { type: "issue", label: "产品评审", parent_id: null } },
      { id: "remote", data: { type: "issue", label: "远程办公", parent_id: "root" } },
      { id: "remote-evidence", data: { type: "evidence", label: "通勤时间与工作效率", parent_id: "remote", metadata_refs: ["topic_u_remote"] } },
      { id: "office", data: { type: "issue", label: "固定坐班", parent_id: "root" } },
      { id: "office-point", data: { type: "point", label: "面对面协作和现场沟通", parent_id: "office", metadata_refs: ["topic_u_office"] } },
      { id: "decision", data: { type: "conclusion", label: "远程办公减少通勤并提升效率", parent_id: "office", metadata_refs: ["topic_u_decision"] } },
    ];
    const panel = mountDecisionPanel(() => undefined, undefined, undefined, undefined, undefined,
      "mtg_topic_dismissal");
    await panel.update(cells);

    const list = document.getElementById("decision-card-list")!;
    expect(list.querySelector(".topic-assignment-suggestion")?.textContent).toContain("远程办公");
    list.querySelector<HTMLButtonElement>(".decision-topic-suggestions .candidate-dismiss")!.click();
    expect(list.querySelector(".topic-assignment-suggestion")).toBeNull();

    const refreshed = mountDecisionPanel(() => undefined, undefined, undefined, undefined, undefined,
      "mtg_topic_dismissal");
    await refreshed.update(cells);
    expect(document.querySelector(".topic-assignment-suggestion")).toBeNull();
    document.querySelector<HTMLButtonElement>(".decision-topic-suggestions .candidate-restore")!.click();
    expect(document.querySelector(".topic-assignment-suggestion")).not.toBeNull();
    localStorage.removeItem(storageKey);
  });
});
