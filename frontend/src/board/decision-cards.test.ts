import { describe, expect, it } from "vitest";
import { projectDecisionCards } from "./decision-cards";
import * as decisionCards from "./decision-cards";

describe("projectDecisionCards", () => {
  it("keeps an AI conclusion pending and separates supporting and opposing evidence", () => {
    const cards = projectDecisionCards([
      { id: "decision", data: { type: "conclusion", label: "先试点", status: "proposed", metadata_refs: ["utt_1"] } },
      { id: "for", data: { type: "point", label: "成本较低", metadata_refs: ["utt_2"] } },
      { id: "against", data: { type: "point", label: "仍有隐私风险", metadata_refs: ["utt_3"] } },
      { id: "support-edge", shape: "edge", source: "for", target: "decision", data: { relation: "support" } },
      { id: "oppose-edge", shape: "edge", source: "against", target: "decision", data: { relation: "oppose" } },
    ]);
    expect(cards).toEqual([{
      id: "decision", label: "先试点", topic: null, status: "pending", sourceRefs: ["utt_1"],
      sourceQuotes: [],
      supports: [{ id: "for", label: "成本较低", sourceRefs: ["utt_2"], quotes: [] }],
      objections: [{ id: "against", label: "仍有隐私风险", sourceRefs: ["utt_3"], quotes: [] }],
      missingEvidence: false,
      reviewFlags: ["存在反对依据", "待人工确认", "议题归属待核对"],
    }]);
  });

  it("marks unsupported conclusions as missing evidence instead of confirmed", () => {
    const cards = projectDecisionCards([
      { id: "decision", data: { type: "conclusion", label: "发布", status: "confirmed" } },
    ]);
    expect(cards[0].status).toBe("pending");
    expect(cards[0].missingEvidence).toBe(true);
  });

  it("requires an audited human status change before showing a confirmed decision", () => {
    const cards = projectDecisionCards([{
      id: "decision", data: { type: "conclusion", label: "发布", status: "confirmed",
        manual_override: { status: true } },
    }]);
    expect(cards[0].status).toBe("confirmed");
  });

  it("joins decision and opposing evidence to their original utterance text and speaker", () => {
    const cards = projectDecisionCards([
      { id: "decision", data: { type: "conclusion", label: "先试点", metadata_refs: ["u_decision"] } },
      { id: "against", data: { type: "point", label: "仍有隐私风险", metadata_refs: ["u_against"] } },
      { id: "oppose-edge", shape: "edge", source: "against", target: "decision", data: { relation: "oppose" } },
    ], new Map([
      ["u_decision", { text: "先在两个团队做小范围试点。", display_name: "主持人" }],
      ["u_against", { text: "员工隐私数据仍然无法保证隔离。", display_name: "法务" }],
    ]));

    expect(cards[0].sourceQuotes).toEqual([{ metaId: "u_decision", text: "先在两个团队做小范围试点。", speaker: "主持人" }]);
    expect(cards[0].objections[0].quotes).toEqual([{ metaId: "u_against", text: "员工隐私数据仍然无法保证隔离。", speaker: "法务" }]);
    expect(cards[0].reviewFlags).toContain("存在反对依据");
  });

  it("shows the nearest issue topic that owns a decision candidate", () => {
    const cards = projectDecisionCards([
      { id: "root", data: { type: "issue", label: "产品评审", parent_id: null } },
      { id: "topic", data: { type: "issue", label: "远程办公试点", parent_id: "root" } },
      { id: "point", data: { type: "point", label: "先从小团队开始", parent_id: "topic" } },
      { id: "decision", data: { type: "conclusion", label: "先试点", parent_id: "point" } },
    ]);
    expect(cards[0].topic).toEqual({ id: "topic", label: "远程办公试点" });
  });

  it("suggests a matching issue for an unassigned decision but never for an assigned one", () => {
    const find = (decisionCards as any).findPossibleTopicAssignments;
    expect(typeof find).toBe("function");
    const cells = [
      { id: "root", data: { type: "issue", label: "会议", parent_id: null } },
      { id: "customer", data: { type: "issue", label: "客户回访与服务质量", parent_id: "root" } },
      { id: "followup", data: { type: "point", label: "回访需要形成问题闭环", parent_id: "customer" } },
      { id: "mobile", data: { type: "issue", label: "移动端版本发布", parent_id: "root" } },
      { id: "decision", data: { type: "conclusion", label: "客户回访增加问题闭环", parent_id: "root" } },
      { id: "assigned", data: { type: "conclusion", label: "客户回访增加问题闭环", parent_id: "customer" } },
    ];

    const suggestions = find(cells);

    expect(suggestions).toHaveLength(1);
    expect(suggestions[0].decisionId).toBe("decision");
    expect(suggestions[0].topicId).toBe("customer");
    expect(suggestions[0].matchingTerms.length).toBeGreaterThanOrEqual(2);
  });

  it("flags a conclusion when its assigned issue conflicts with its evidence context", () => {
    const find = (decisionCards as any).findPossibleTopicAssignments;
    const metadata = new Map([
      ["u_remote", { kind: "utt", text: "远程办公能减少通勤时间，改善员工日常效率。" }],
      ["u_office", { kind: "utt", text: "固定坐班便于面对面协作和现场沟通。" }],
      ["u_decision", { kind: "utt", text: "远程办公减少通勤时间，让员工能更高效地安排工作。" }],
    ]);
    const cells = [
      { id: "root", data: { type: "issue", label: "产品评审", parent_id: null } },
      { id: "remote", data: { type: "issue", label: "远程办公", parent_id: "root" } },
      { id: "remote-evidence", data: { type: "evidence", label: "通勤时间与工作效率", parent_id: "remote", metadata_refs: ["u_remote"] } },
      { id: "office", data: { type: "issue", label: "固定坐班", parent_id: "root" } },
      { id: "office-point", data: { type: "point", label: "面对面协作和现场沟通", parent_id: "office", metadata_refs: ["u_office"] } },
      { id: "decision", data: { type: "conclusion", label: "远程办公减少通勤并提升效率", parent_id: "office", metadata_refs: ["u_decision"] } },
    ];

    const suggestions = find(cells, metadata);

    expect(suggestions).toEqual(expect.arrayContaining([expect.objectContaining({
      decisionId: "decision", topicId: "remote",
    })]));
  });

  it("suggests only high-overlap duplicate wording and excludes explicitly opposed nodes", () => {
    const find = (decisionCards as any).findPossibleDuplicates;
    expect(typeof find).toBe("function");
    const suggestions = find([
      { id: "one", data: { type: "point", label: "远程办公能提升协作效率" } },
      { id: "two", data: { type: "point", label: "远程办公提升协作效率" } },
      { id: "three", data: { type: "point", label: "远程办公降低协作效率" } },
      { id: "oppose", shape: "edge", source: "one", target: "three", data: { relation: "oppose" } },
    ]);
    expect(suggestions).toHaveLength(1);
    expect(new Set([suggestions[0].leftId, suggestions[0].rightId])).toEqual(new Set(["one", "two"]));
    expect(suggestions[0].score).toBeGreaterThanOrEqual(0.78);
    const oppositeWording = find([
      { id: "positive", data: { type: "point", label: "远程办公提升协作效率" } },
      { id: "negative", data: { type: "point", label: "远程办公不提升协作效率" } },
    ]);
    expect(oppositeWording).toHaveLength(0);
  });

  it("caps duplicate review hints at the requested small queue size", () => {
    const find = (decisionCards as any).findPossibleDuplicates;
    const cells = Array.from({ length: 5 }, (_, index) => ({
      id: `same-${index}`, data: { type: "point", label: "每周五复盘远程试点并汇总故障" },
    }));
    expect(find(cells, 0.78, new Map(), 3)).toHaveLength(3);
    expect(find(cells, 0.78, new Map(), 0)).toHaveLength(0);
  });

  it("uses linked source utterances to find duplicate claims with different labels", () => {
    const find = (decisionCards as any).findPossibleDuplicates;
    const metadata = new Map([
      ["u1", { text: "每周五复盘远程试点并汇总故障", display_name: "甲" }],
      ["u2", { text: "周五复盘远程试点并汇总故障", display_name: "乙" }],
    ]);
    const suggestions = find([
      { id: "one", data: { type: "point", label: "复盘远程试点", metadata_refs: ["u1"] } },
      { id: "two", data: { type: "point", label: "评估试点表现", metadata_refs: ["u2"] } },
    ], 0.78, metadata);

    expect(suggestions).toHaveLength(1);
    expect(suggestions[0].scoreBasis).toBe("source");
    expect(suggestions[0].score).toBeGreaterThanOrEqual(0.78);
  });

  it("suggests differently worded claims that repeat the same measure under one issue", () => {
    const find = (decisionCards as any).findPossibleDuplicates;
    const metadata = new Map([
      ["u_commute", { text: "远程办公把每天两小时通勤还给员工。", display_name: "甲" }],
      ["u_time", { text: "远程工作者每天多出两小时陪伴家人。", display_name: "乙" }],
    ]);
    const suggestions = find([
      { id: "root", data: { type: "issue", label: "会议", parent_id: null } },
      { id: "topic", data: { type: "issue", label: "远程办公", parent_id: "root" } },
      { id: "commute", data: { type: "evidence", label: "减少通勤时间", parent_id: "topic", metadata_refs: ["u_commute"] } },
      { id: "time", data: { type: "evidence", label: "增加家庭陪伴", parent_id: "topic", metadata_refs: ["u_time"] } },
    ], 0.78, metadata);

    expect(suggestions).toHaveLength(1);
    expect(new Set([suggestions[0].leftId, suggestions[0].rightId])).toEqual(new Set(["commute", "time"]));
    expect(suggestions[0].signal).toBe("shared-measure");
    expect(suggestions[0].matchingTerms).toContain("两小时");
  });

  it("ranks repeated-position pairs by source overlap instead of node id", () => {
    const find = (decisionCards as any).findPossibleDuplicates;
    const metadata = new Map([
      ["u-a", { kind: "utt", text: "我方认为固定坐班是协作效率与执行力的根基，远程只是权宜之计。" }],
      ["u-b", { kind: "utt", text: "我方认为办公室加强新人培养，线下办公提升协作效率。" }],
      ["u-z", { kind: "utt", text: "综上，固定坐班不可替代，是协作效率与执行力的根基。" }],
    ]);
    const suggestions = find([
      { id: "root", data: { type: "issue", label: "辩论", parent_id: null } },
      { id: "topic", data: { type: "issue", label: "远程办公与固定坐班", parent_id: "root" } },
      { id: "a", data: { type: "point", label: "固定坐班协作根基", parent_id: "topic", metadata_refs: ["u-a"] } },
      { id: "b", data: { type: "point", label: "办公室新人培养", parent_id: "topic", metadata_refs: ["u-b"] } },
      { id: "z", data: { type: "point", label: "固定坐班协作效率与执行力的根基", parent_id: "topic", metadata_refs: ["u-z"] } },
    ], 0.78, metadata);

    expect(new Set([suggestions[0].leftId, suggestions[0].rightId])).toEqual(new Set(["a", "z"]));
  });

  it("does not treat a shared year or work-schedule length as a duplicate claim", () => {
    const find = (decisionCards as any).findPossibleDuplicates;
    const pair = (left: string, right: string, labels: [string, string]) => {
      const metadata = new Map([
        ["u-left", { kind: "utt", text: left }],
        ["u-right", { kind: "utt", text: right }],
      ]);
      return find([
        { id: "root", data: { type: "issue", label: "会议", parent_id: null } },
        { id: "topic", data: { type: "issue", label: "远程办公安排", parent_id: "root" } },
        { id: "left", data: { type: "evidence", label: labels[0], parent_id: "topic", metadata_refs: ["u-left"] } },
        { id: "right", data: { type: "evidence", label: labels[1], parent_id: "topic", metadata_refs: ["u-right"] } },
      ], 0.78, metadata);
    };

    expect(pair(
      "团建一年几次，工位是天天相处，日积月累的默契才是硬通货。",
      "安全产品是规模经济，SaaS 化之后中小企业一年几千块就能用上企业级防护。",
      ["团队互动与办公室默契", "SaaS安全产品价格"],
    )).toHaveLength(0);
    expect(pair(
      "MIT 后续研究显示每周两到三天远程的团队，创造力与全职远程相当。",
      "碎片化是排班没做好，微软的三天到岗制就平衡得很好。",
      ["每周远程安排与团队创造力", "微软混合到岗安排"],
    )).toHaveLength(0);
    expect(pair(
      "每季度见一次的同事能叫同事吗，泛泛之交撑不起深夜救火的信任。",
      "感谢计时员的辛劳，全场控时精准没有一次超时。",
      ["团队互动与办公室默契", "赛程时间控制"],
    )).toHaveLength(0);
    expect(pair(
      "孤独的解法是社群运营与定期线下聚合，每季度一次聚会，成本远低于整栋办公楼。",
      "散会前再提醒一次，纪要与金句整理将发到全员频道。",
      ["远程团队关系建设", "会议纪要安排"],
    )).toHaveLength(0);
  });

  it("surfaces same-topic opposite positions for human confirmation", () => {
    const find = (decisionCards as any).findPossibleOppositions;
    expect(typeof find).toBe("function");
    const suggestions = find([
      { id: "root", data: { type: "issue", label: "产品评审", parent_id: null } },
      { id: "topic", data: { type: "issue", label: "远程办公", parent_id: "root" } },
      { id: "for", data: { type: "point", label: "远程办公提升协作效率", parent_id: "topic" } },
      { id: "against", data: { type: "point", label: "远程办公不提升协作效率", parent_id: "topic" } },
      { id: "unrelated", data: { type: "point", label: "远程办公不提升协作效率", parent_id: "root" } },
    ]);
    expect(suggestions).toHaveLength(1);
    expect(new Set([suggestions[0].leftId, suggestions[0].rightId])).toEqual(new Set(["for", "against"]));
    expect(suggestions[0].score).toBeGreaterThanOrEqual(0.55);
    expect(suggestions[0].topicLabel).toBe("远程办公");
  });

  it("honors the requested result limit for opposition review hints", () => {
    const find = (decisionCards as any).findPossibleOppositions;
    const cells = [
      { id: "root", data: { type: "issue", label: "产品评审", parent_id: null } },
      { id: "topic", data: { type: "issue", label: "远程办公", parent_id: "root" } },
      ...["a", "b", "c"].map((id) => ({ id, data: { type: "point", label: "远程办公提升协作效率", parent_id: "topic" } })),
      { id: "d", data: { type: "point", label: "远程办公不提升协作效率", parent_id: "topic" } },
    ];

    expect(find(cells, 0.55, new Map(), 1)).toHaveLength(1);
    expect(find(cells, 0.55, new Map(), 0)).toHaveLength(0);
  });

  it("finds opposition in linked source utterances when the node labels are generic", () => {
    const find = (decisionCards as any).findPossibleOppositions;
    const metadata = new Map([
      ["u_for", { kind: "utt", text: "建议提高远程办公比例，以提升团队协作效率。" }],
      ["u_against", { kind: "utt", text: "不建议提高远程办公比例，会降低团队协作效率。" }],
    ]);
    const suggestions = find([
      { id: "root", data: { type: "issue", label: "产品评审", parent_id: null } },
      { id: "topic", data: { type: "issue", label: "远程办公", parent_id: "root" } },
      { id: "for", data: { type: "point", label: "方案倾向", parent_id: "topic", metadata_refs: ["u_for"] } },
      { id: "against", data: { type: "point", label: "方案评估", parent_id: "topic", metadata_refs: ["u_against"] } },
    ], 0.55, metadata);

    expect(suggestions).toHaveLength(1);
    expect(new Set([suggestions[0].leftId, suggestions[0].rightId])).toEqual(new Set(["for", "against"]));
    expect(suggestions[0].scoreBasis).toBe("source");
    expect(suggestions[0].matchingTerms).toContain("远程");
  });

  it("suggests an evidence claim and an explicit source rebuttal for human review", () => {
    const find = (decisionCards as any).findPossibleOppositions;
    const metadata = new Map([
      ["u_evidence", { kind: "utt", text: "斯坦福研究显示远程员工绩效提升百分之十三。" }],
      ["u_rebuttal", { kind: "utt", text: "那份研究样本是呼叫中心，推演到所有岗位是以偏概全。" }],
      ["u_distractor", { kind: "utt", text: "市中心写字楼租金较高，企业可以节省成本。" }],
      ["u_weak_rebuttal", { kind: "utt", text: "这个推论不成立，研究还需要继续讨论。" }],
    ]);
    const suggestions = find([
      { id: "root", data: { type: "issue", label: "会议", parent_id: null } },
      { id: "topic", data: { type: "issue", label: "远程办公", parent_id: "root" } },
      { id: "evidence", data: { type: "evidence", label: "绩效提升数据", parent_id: "topic", metadata_refs: ["u_evidence"] } },
      { id: "rebuttal", data: { type: "point", label: "样本范围质疑", parent_id: "topic", metadata_refs: ["u_rebuttal"] } },
      { id: "distractor", data: { type: "point", label: "办公成本", parent_id: "topic", metadata_refs: ["u_distractor"] } },
      { id: "weak-rebuttal", data: { type: "point", label: "研究意见", parent_id: "topic", metadata_refs: ["u_weak_rebuttal"] } },
    ], 0.55, metadata);

    expect(suggestions.map((item: any) => [item.leftId, item.rightId])).toEqual([["evidence", "rebuttal"]]);
    expect(suggestions).toHaveLength(1);
    expect(new Set([suggestions[0].leftId, suggestions[0].rightId])).toEqual(new Set(["evidence", "rebuttal"]));
    expect(suggestions[0].scoreBasis).toBe("source");
    expect(suggestions[0].signal).toBe("explicit-rebuttal");
    expect(suggestions[0].matchingTerms).toContain("研究");
    expect(suggestions[0].score).toBeGreaterThanOrEqual(0.8);
    expect(suggestions).not.toContainEqual(expect.objectContaining({
      leftId: "evidence", rightId: "weak-rebuttal",
    }));
  });

  it("surfaces explicit rebuttals across different topic assignments and flags the mismatch", () => {
    const find = (decisionCards as any).findPossibleOppositions;
    const metadata = new Map([
      ["u_old", { kind: "utt", text: "关键决策常发生在茶水间和工位旁的非正式交流里，远程丢掉了这些瞬间。" }],
      ["u_reply", { kind: "utt", text: "对方说非正式交流重要，可线上同样有虚拟茶水间和群聊闲聊，只是形式变了。" }],
    ]);
    const suggestions = find([
      { id: "root", data: { type: "issue", label: "会议", parent_id: null } },
      { id: "office", data: { type: "issue", label: "固定坐班", parent_id: "root" } },
      { id: "remote", data: { type: "issue", label: "远程办公", parent_id: "root" } },
      { id: "old-claim", data: { type: "point", label: "非正式交流影响决策", parent_id: "office", metadata_refs: ["u_old"] } },
      { id: "reply", data: { type: "evidence", label: "线上虚拟茶水间", parent_id: "remote", metadata_refs: ["u_reply"] } },
    ], 0.55, metadata);

    expect(suggestions).toHaveLength(1);
    expect(suggestions[0].signal).toBe("explicit-rebuttal");
    expect(suggestions[0].scopeMismatch).toBe(true);
    expect(suggestions[0].leftTopicLabel).not.toBe(suggestions[0].rightTopicLabel);
  });

  it("keeps a rare opposition signal visible when explicit-rebuttal candidates dominate", () => {
    const find = (decisionCards as any).findPossibleOppositions;
    const sources = new Map<string, { kind: string; text: string }>();
    const utterances = [
      ...Array.from({ length: 6 }, (_, index) => ({
        id: `claim-${index}`, text: "研究报告显示远程员工绩效提升百分之十三，样本来自呼叫中心。",
      })),
      ...Array.from({ length: 6 }, (_, index) => ({
        id: `reply-${index}`, text: "那份研究样本是呼叫中心，推演到所有岗位是以偏概全。",
      })),
      { id: "remote", text: "我方坚持远程办公能提升团队效率，是适合未来的工作方式。" },
      { id: "office", text: "我方认为固定坐班是协作效率与执行力的根基。" },
    ];
    const cells = [
      { id: "root", data: { type: "issue", label: "评审", parent_id: null } },
      { id: "topic", data: { type: "issue", label: "工作方式", parent_id: "root" } },
      ...utterances.map(({ id, text }, index) => {
        sources.set(id, { kind: "utt", text });
        return { id: `n${index}`, data: { type: "point", label: `发言${index}`, parent_id: "topic", metadata_refs: [id] } };
      }),
    ];

    const suggestions = find(cells, 0.55, sources);
    const visibleSignals = new Set(suggestions.map((item: { signal: string }) => item.signal));

    expect(suggestions).toHaveLength(20);
    expect(visibleSignals).toContain("explicit-rebuttal");
    expect(visibleSignals).toContain("position");
  });

  it("does not broaden plain polarity hints across different topic assignments", () => {
    const find = (decisionCards as any).findPossibleOppositions;
    const suggestions = find([
      { id: "root", data: { type: "issue", label: "会议", parent_id: null } },
      { id: "office", data: { type: "issue", label: "固定坐班", parent_id: "root" } },
      { id: "remote", data: { type: "issue", label: "远程办公", parent_id: "root" } },
      { id: "one", data: { type: "point", label: "远程办公提升协作效率", parent_id: "office" } },
      { id: "two", data: { type: "point", label: "远程办公不提升协作效率", parent_id: "remote" } },
    ]);
    expect(suggestions).toHaveLength(0);
  });

  it("uses an explicit rebuttal to connect loosely worded root-level evidence claims", () => {
    const find = (decisionCards as any).findPossibleOppositions;
    const metadata = new Map([
      ["u_study", { kind: "utt", text: "斯坦福研究显示远程员工绩效提升百分之十三，离职率减半。" }],
      ["u_rebuttal", { kind: "utt", text: "那份研究样本是呼叫中心，任务高度标准化，推演到所有岗位是以偏概全。" }],
    ]);
    const suggestions = find([
      { id: "n_issue_root", data: { type: "issue", label: "会议", parent_id: null } },
      { id: "study", data: { type: "evidence", label: "远程绩效数据", parent_id: "n_issue_root", metadata_refs: ["u_study"] } },
      { id: "rebuttal", data: { type: "point", label: "样本范围质疑", parent_id: "n_issue_root", metadata_refs: ["u_rebuttal"] } },
    ], 0.55, metadata);

    expect(suggestions).toHaveLength(1);
    expect(suggestions[0].signal).toBe("explicit-rebuttal");
    expect(suggestions[0].matchingTerms).toContain("研究");
    expect(suggestions[0].topicLabel).toBe("未分配议题");
  });

  it("does not treat moderator summaries that merely mention rebuttal as opposition", () => {
    const find = (decisionCards as any).findPossibleOppositions;
    const metadata = new Map([
      ["u_affirmative_summary", { kind: "utt", text: "正方反驳有力之处在于把坐班的隐性成本摆上台面。" }],
      ["u_negative_summary", { kind: "utt", text: "反方反驳有力之处在于指出远程优势大多可由混合模式兑现，不必全远程。" }],
      ["u_unrelated", { kind: "utt", text: "员工今天讨论了办公楼租金与团队协作。" }],
    ]);
    const cells = [
      { id: "n_issue_root", data: { type: "issue", label: "会议", parent_id: null } },
      { id: "affirmative", data: { type: "point", label: "正方总结", parent_id: "n_issue_root", metadata_refs: ["u_affirmative_summary"] } },
      { id: "negative", data: { type: "point", label: "反方总结", parent_id: "n_issue_root", metadata_refs: ["u_negative_summary"] } },
      { id: "unrelated", data: { type: "point", label: "员工办公讨论", parent_id: "n_issue_root", metadata_refs: ["u_unrelated"] } },
    ];

    const suggestions = find(cells, 0.55, metadata);

    expect(suggestions).not.toContainEqual(expect.objectContaining({
      leftId: "affirmative", rightId: "negative", signal: "explicit-rebuttal",
    }));
    expect(suggestions).not.toContainEqual(expect.objectContaining({
      leftId: "affirmative", rightId: "unrelated", signal: "explicit-rebuttal",
    }));
  });

  it("recognizes opposing remote-work and office stances from their source wording", () => {
    const find = (decisionCards as any).findPossibleOppositions;
    const metadata = new Map([
      ["u_remote", { kind: "utt", text: "谢谢主席。我方认为远程办公是效率与人本管理的双重胜利，理由有三。" }],
      ["u_office", { kind: "utt", text: "谢谢主席。我方认为固定坐班是协作效率与执行力的根基，远程只是权宜之计。" }],
    ]);
    const suggestions = find([
      { id: "n_issue_root", data: { type: "issue", label: "会议", parent_id: null } },
      { id: "remote", data: { type: "point", label: "远程办公观点", parent_id: "n_issue_root", metadata_refs: ["u_remote"] } },
      { id: "office", data: { type: "point", label: "固定坐班观点", parent_id: "n_issue_root", metadata_refs: ["u_office"] } },
    ], 0.55, metadata);

    expect(suggestions).toHaveLength(1);
    expect(suggestions[0].signal).toBe("position");
    expect(suggestions[0].matchingTerms).toContain("效率");
    expect(suggestions[0].matchingTerms).not.toContain("谢谢主席");
    expect(suggestions[0].score).toBeGreaterThan(0.56);
  });

  it("connects a cited security risk to a source-backed countermeasure", () => {
    const find = (decisionCards as any).findPossibleOppositions;
    const metadata = new Map([
      ["u_risk", { kind: "utt", text: "员工在家里用私人网络处理核心代码与客户数据，泄露风险成倍放大。" }],
      ["u_control", { kind: "utt", text: "VPN、零信任架构、终端管控早已成熟，金融行业全员远程的先例都跑通了安全体系。" }],
    ]);
    const suggestions = find([
      { id: "n_issue_root", data: { type: "issue", label: "会议", parent_id: null } },
      { id: "risk", data: { type: "point", label: "私人网络泄露风险", parent_id: "n_issue_root", metadata_refs: ["u_risk"] } },
      { id: "controls", data: { type: "point", label: "安全防护措施", parent_id: "n_issue_root", metadata_refs: ["u_control"] } },
    ], 0.55, metadata);

    expect(suggestions).toHaveLength(1);
    expect(suggestions[0].signal).toBe("countermeasure");
  });

  it("connects positive wellbeing claims to a source-backed loneliness objection", () => {
    const find = (decisionCards as any).findPossibleOppositions;
    const metadata = new Map([
      ["u_happy", { kind: "utt", text: "员工体验上，远程者每天多出两小时可以健身陪家人，幸福感显著提升。" }],
      ["u_lonely", { kind: "utt", text: "幸福感背后是孤岛感，行业调研里远程员工最大痛点是孤独，排名超过效率担忧。" }],
    ]);
    const suggestions = find([
      { id: "n_issue_root", data: { type: "issue", label: "会议", parent_id: null } },
      { id: "happy", data: { type: "evidence", label: "陪伴和幸福感", parent_id: "n_issue_root", metadata_refs: ["u_happy"] } },
      { id: "lonely", data: { type: "evidence", label: "孤独是主要痛点", parent_id: "n_issue_root", metadata_refs: ["u_lonely"] } },
    ], 0.55, metadata);

    expect(suggestions).toHaveLength(1);
    expect(suggestions[0].signal).toBe("outcome-contrast");
  });

  it("surfaces repeated debate positions as duplicate hints", () => {
    const find = (decisionCards as any).findPossibleDuplicates;
    const metadata = new Map([
      ["u_first", { kind: "utt", text: "我方认为固定坐班是协作效率与执行力的根基，远程只是权宜之计。" }],
      ["u_summary", { kind: "utt", text: "纵观全场，我方坚持固定坐班不可替代，理由有三。" }],
    ]);
    const suggestions = find([
      { id: "n_issue_root", data: { type: "issue", label: "会议", parent_id: null } },
      { id: "first", data: { type: "point", label: "固定坐班的根基作用", parent_id: "n_issue_root", metadata_refs: ["u_first"] } },
      { id: "summary", data: { type: "point", label: "固定坐班不可替代", parent_id: "n_issue_root", metadata_refs: ["u_summary"] } },
    ], 0.78, metadata);

    expect(suggestions).toHaveLength(1);
    expect(suggestions[0].signal).toBe("repeated-position");
  });

  it("puts decisions with missing evidence or unresolved objections before routine cards", () => {
    const cards = projectDecisionCards([
      { id: "routine", data: { type: "conclusion", label: "常规结论", metadata_refs: ["u1"] } },
      { id: "needs-review", data: { type: "conclusion", label: "证据不足结论" } },
      { id: "support", data: { type: "point", label: "支持观点", metadata_refs: ["u2"] } },
      { id: "oppose", data: { type: "point", label: "反对观点", metadata_refs: ["u3"] } },
      { id: "supports", shape: "edge", source: "support", target: "routine", data: { relation: "support" } },
      { id: "objection", shape: "edge", source: "oppose", target: "routine", data: { relation: "oppose" } },
    ]);
    expect(cards[0].id).toBe("needs-review");
  });
});
