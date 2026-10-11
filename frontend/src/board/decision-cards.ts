import { projectBoard } from "./projection";

export interface DecisionEvidence {
  id: string;
  label: string;
  sourceRefs: string[];
  quotes: DecisionQuote[];
}

export interface DecisionQuote {
  metaId: string;
  text: string;
  speaker: string;
}

export interface DecisionTopic {
  id: string;
  label: string;
}

export interface DecisionCard {
  id: string;
  label: string;
  topic: DecisionTopic | null;
  status: "pending" | "confirmed" | "dismissed";
  sourceRefs: string[];
  sourceQuotes: DecisionQuote[];
  supports: DecisionEvidence[];
  objections: DecisionEvidence[];
  missingEvidence: boolean;
  reviewFlags: string[];
}

const refs = (data: Record<string, any>): string[] =>
  Array.isArray(data.metadata_refs)
    ? data.metadata_refs.filter((value: unknown): value is string => typeof value === "string") : [];

type MetadataLike = { text?: string; kind?: string; display_name?: string; speaker_ref?: string };

function quotesFor(refs: string[], metadataById: ReadonlyMap<string, MetadataLike>): DecisionQuote[] {
  return refs.flatMap((metaId) => {
    const record = metadataById.get(metaId);
    return typeof record?.text === "string" && record.text.trim()
      ? [{ metaId, text: record.text, speaker: record.display_name || record.speaker_ref || "未知发言人" }]
      : [];
  });
}

export function projectDecisionCards(cells: any[],
                                    metadataById: ReadonlyMap<string, MetadataLike> = new Map()): DecisionCard[] {
  const board = projectBoard(cells);
  const rootIssueIds = new Set(board.roots.filter((id) => board.nodeById.get(id)?.data.type === "issue"));
  const cards = board.conclusions.map((decision) => {
    let topic: DecisionTopic | null = null;
    const seenAncestors = new Set<string>();
    let parentId = board.parentById.get(decision.id);
    while (parentId && !seenAncestors.has(parentId)) {
      seenAncestors.add(parentId);
      const parent = board.nodeById.get(parentId);
      if (!parent) break;
      if (parent.data.type === "issue") {
        topic = { id: parent.id, label: String(parent.data.label ?? parent.id) };
        break;
      }
      parentId = board.parentById.get(parentId);
    }
    const supports: DecisionEvidence[] = [];
    const objections: DecisionEvidence[] = [];
    for (const edge of board.semanticEdgesByNode.get(decision.id) ?? []) {
      if (edge.relation !== "support" && edge.relation !== "oppose") continue;
      const otherId = edge.source === decision.id ? edge.target : edge.source;
      const other = board.nodeById.get(otherId);
      if (!other) continue;
      const sourceRefs = refs(other.data);
      const evidence = { id: other.id, label: String(other.data.label ?? "未命名依据"),
        sourceRefs, quotes: quotesFor(sourceRefs, metadataById) };
      (edge.relation === "support" ? supports : objections).push(evidence);
    }
    const sourceRefs = refs(decision.data);
    const humanStatus = decision.data.manual_override?.status === true;
    const status: DecisionCard["status"] = humanStatus && decision.data.status === "confirmed" ? "confirmed"
      : humanStatus && decision.data.status === "dismissed" ? "dismissed" : "pending";
    const missingEvidence = sourceRefs.length === 0 &&
      [...supports, ...objections].every((item) => item.sourceRefs.length === 0);
    const reviewFlags = [
      ...(missingEvidence ? ["缺少原话依据"] : []),
      ...(supports.length === 0 ? ["缺少支持关系"] : []),
      ...(objections.length > 0 ? ["存在反对依据"] : []),
      ...(status === "pending" ? ["待人工确认"] : []),
      ...(!topic || topic.id === "n_issue_root" || rootIssueIds.has(topic.id) ? ["议题归属待核对"] : []),
    ];
    return {
      id: decision.id,
      label: String(decision.data.label ?? "待命名结论"),
      topic,
      status, sourceRefs, sourceQuotes: quotesFor(sourceRefs, metadataById),
      supports, objections, missingEvidence, reviewFlags,
    };
  });
  const reviewPriority = (card: DecisionCard) =>
    (card.missingEvidence ? 4 : 0)
    + (card.objections.length ? 3 : 0)
    + (card.supports.length === 0 ? 2 : 0)
    + (!card.topic || card.topic.id === "n_issue_root" || rootIssueIds.has(card.topic.id) ? 1 : 0)
    + (card.status === "pending" ? 1 : 0);
  return cards.sort((left, right) => reviewPriority(right) - reviewPriority(left));
}

export interface TopicAssignmentSuggestion {
  decisionId: string;
  decisionLabel: string;
  topicId: string;
  topicLabel: string;
  matchingTerms: string[];
  score: number;
}

const assignmentStopwords = new Set([
  "会议", "产品", "项目", "问题", "事项", "方面", "进行", "相关", "目前", "需要", "可以",
  "以及", "由于", "关于", "整体", "我们", "他们", "这个", "一个", "工作", "情况", "建议",
]);

function assignmentFeatures(value: string): Set<string> {
  return new Set([...lexicalFeatures(value).features]
    .filter((feature) => feature.length > 1 && !assignmentStopwords.has(feature)));
}

function referencedTextFeatures(data: Record<string, any>, metadataById: ReadonlyMap<string, MetadataLike>): Set<string> {
  const values = [data.label, data.description].filter((value): value is string => typeof value === "string");
  for (const metaId of refs(data)) {
    const record = metadataById.get(metaId);
    if (record?.kind !== "summary" && typeof record?.text === "string") values.push(record.text);
  }
  return new Set(values.flatMap((value) => [...assignmentFeatures(value)]));
}

/** Suggests placements for unassigned conclusions and clear issue mismatches; suggestions never mutate the board. */
export function findPossibleTopicAssignments(
  cells: any[],
  metadataById: ReadonlyMap<string, MetadataLike> = new Map(),
  threshold = 0.22,
  limit = 3,
): TopicAssignmentSuggestion[] {
  const board = projectBoard(cells ?? []);
  const rootIssueIds = new Set(board.roots.filter((id) => board.nodeById.get(id)?.data.type === "issue"));
  const isRootIssue = (id: string) => id === "n_issue_root" || rootIssueIds.has(id);
  const topics = board.nodes.filter((node) => node.data.type === "issue" && !isRootIssue(node.id));
  if (!topics.length) return [];

  const topicFeatures = new Map<string, Set<string>>();
  const topicFrequencyFeatures = new Map<string, Set<string>>();
  const topicLabels = new Map<string, Set<string>>();
  const topicNodeIds = new Map<string, Set<string>>();
  for (const topic of topics) {
    const scoped = new Set<string>();
    const frequencyScoped = new Set<string>();
    const labels = assignmentFeatures(String(topic.data.label ?? topic.id));
    const stack = [topic.id];
    const visited = new Set<string>();
    while (stack.length) {
      const id = stack.pop()!;
      if (visited.has(id)) continue;
      visited.add(id);
      const node = board.nodeById.get(id);
      if (!node) continue;
      const features = referencedTextFeatures(node.data, metadataById);
      for (const feature of features) scoped.add(feature);
      // Existing conclusions can repeat the item being evaluated and make its
      // own words look generic. Estimate topic frequency from the issue and
      // its supporting content instead.
      if (node.data.type !== "conclusion") {
        for (const feature of features) frequencyScoped.add(feature);
      }
      for (const childId of board.children.get(id) ?? []) stack.push(childId);
    }
    topicFeatures.set(topic.id, scoped);
    topicFrequencyFeatures.set(topic.id, frequencyScoped);
    topicLabels.set(topic.id, labels);
    topicNodeIds.set(topic.id, visited);
  }

  const documentFrequency = new Map<string, number>();
  for (const features of topicFrequencyFeatures.values()) for (const feature of features) {
    documentFrequency.set(feature, (documentFrequency.get(feature) ?? 0) + 1);
  }
  const commonFrequency = Math.max(2, Math.ceil(topics.length * 0.8));
  const weight = (feature: string) => Math.log((topics.length + 1) / ((documentFrequency.get(feature) ?? 0) + 1)) + 1;
  const featuresForAssignment = (topicId: string, excludedNodeId: string): Set<string> => {
    const nodeIds = topicNodeIds.get(topicId);
    if (!nodeIds?.has(excludedNodeId)) return topicFeatures.get(topicId) ?? new Set<string>();
    const scoped = new Set<string>();
    for (const nodeId of nodeIds) {
      if (nodeId === excludedNodeId) continue;
      const node = board.nodeById.get(nodeId);
      if (node) for (const feature of referencedTextFeatures(node.data, metadataById)) scoped.add(feature);
    }
    return scoped;
  };
  const toCurrentIssue = (nodeId: string): string => {
    const visited = new Set<string>();
    let parentId = board.parentById.get(nodeId);
    while (parentId && !visited.has(parentId)) {
      visited.add(parentId);
      const parent = board.nodeById.get(parentId);
      if (!parent) break;
      if (parent.data.type === "issue") return parent.id;
      parentId = board.parentById.get(parentId);
    }
    return "";
  };
  const candidates: TopicAssignmentSuggestion[] = [];
  for (const decision of board.conclusions) {
    const foundTopicId = toCurrentIssue(decision.id);
    const currentTopicId = isRootIssue(foundTopicId) ? "" : foundTopicId;
    const context = referencedTextFeatures(decision.data, metadataById);
    for (const edge of board.semanticEdgesByNode.get(decision.id) ?? []) {
      if (edge.relation !== "support" && edge.relation !== "oppose") continue;
      const otherId = edge.source === decision.id ? edge.target : edge.source;
      const related = board.nodeById.get(otherId);
      if (related) for (const feature of referencedTextFeatures(related.data, metadataById)) context.add(feature);
    }
    const informativeContext = [...context].filter((feature) =>
      (documentFrequency.get(feature) ?? 0) < commonFrequency);
    const totalWeight = informativeContext.reduce((total, feature) => total + weight(feature), 0);
    if (informativeContext.length < 2 || totalWeight === 0) continue;

    const scoreForTopic = (topicId: string) => {
      const scoped = featuresForAssignment(topicId, decision.id);
      const matchingTerms = informativeContext.filter((feature) => scoped.has(feature));
      if (matchingTerms.length < 2) return null;
      const score = matchingTerms.reduce((total, feature) => total + weight(feature), 0) / totalWeight;
      if (score < threshold) return null;
      return { score, matchingTerms };
    };
    const currentScore = currentTopicId ? scoreForTopic(currentTopicId)?.score ?? 0 : 0;

    for (const topic of topics) {
      if (topic.id === currentTopicId) continue;
      const match = scoreForTopic(topic.id);
      if (!match) continue;
      // A placed conclusion is only flagged when another issue is a clearly better fit.
      if (currentTopicId && match.score < currentScore + 0.15) continue;
      const matchingTerms = match.matchingTerms;
      const labelFeatures = topicLabels.get(topic.id)!;
      matchingTerms.sort((left, right) => Number(labelFeatures.has(right)) - Number(labelFeatures.has(left))
        || weight(right) - weight(left) || left.localeCompare(right));
      candidates.push({
        decisionId: decision.id,
        decisionLabel: String(decision.data.label ?? "待命名结论"),
        topicId: topic.id,
        topicLabel: String(topic.data.label ?? topic.id),
        matchingTerms: matchingTerms.slice(0, 8),
        score: Math.round(match.score * 1000) / 1000,
      });
    }
  }
  const byDecision = new Map<string, TopicAssignmentSuggestion[]>();
  for (const suggestion of candidates.sort((left, right) => right.score - left.score
    || left.topicId.localeCompare(right.topicId))) {
    const matches = byDecision.get(suggestion.decisionId) ?? [];
    if (matches.length >= limit) continue;
    matches.push(suggestion);
    byDecision.set(suggestion.decisionId, matches);
  }
  return [...byDecision.values()].flat();
}

export interface PossibleDuplicate {
  leftId: string;
  leftLabel: string;
  rightId: string;
  rightLabel: string;
  scoreBasis: "label" | "source";
  signal: "similar-wording" | "shared-measure" | "repeated-position";
  matchingTerms: string[];
  score: number;
}

function lexicalFeatures(value: string): { normalized: string; features: Set<string> } {
  const normalized = value.normalize("NFKC").toLocaleLowerCase().replace(/[^\p{L}\p{N}]/gu, "");
  const features = new Set<string>();
  for (const token of normalized.match(/[\u3400-\u9fff]+|[a-z0-9]+/g) ?? []) {
    if (/^[\u3400-\u9fff]+$/.test(token)) {
      if (token.length === 1) features.add(token);
      else for (let index = 0; index < token.length - 1; index += 1) features.add(token.slice(index, index + 2));
    } else features.add(token);
  }
  return { normalized, features };
}

function measureAnchors(value: string): Set<string> {
  const normalized = value.normalize("NFKC").toLocaleLowerCase();
  return new Set(normalized.match(/(?:\d+(?:\.\d+)?|[零〇一二两三四五六七八九十百千万半]+)(?:小时|分钟|秒|天|周|月|年|人|次|元|公里|千米|%|个百分点)/g) ?? []);
}

function isGenericScheduleMeasure(value: string): boolean {
  // Shared schedule duration alone does not make claims duplicates: e.g. remote
  // days and office days can describe competing work arrangements.
  return /(?:天|周|月|年)$/u.test(value);
}

function nearestIssueId(board: ReturnType<typeof projectBoard>, nodeId: string): string {
  const seen = new Set<string>();
  let parentId = board.parentById.get(nodeId);
  while (parentId && !seen.has(parentId)) {
    seen.add(parentId);
    const parent = board.nodeById.get(parentId);
    if (!parent) break;
    if (parent.data.type === "issue") return parent.id === "n_issue_root" ? "" : parent.id;
    parentId = board.parentById.get(parentId);
  }
  return "";
}

const sharedMeasureStopFeatures = new Set(["远程", "办公", "员工", "团队", "会议", "工作", "每天", "小时", "时间"]);
const genericCountFeature = (feature: string) => feature === "每次"
  || /^(?:[0-9零〇一二两三四五六七八九十百千万半]+|几|多|若干)次$/u.test(feature);

function oppositePolarity(left: string, right: string): boolean {
  const negation = (value: string) => /不|未|没|无|不能|无法|并非|没有|不应|不宜/.test(value);
  if (negation(left) !== negation(right)) return true;
  const directions = [
    ["positive", /提升|提高|改善|增强|促进|支持|赞成|采纳|有效/],
    ["negative", /降低|减少|恶化|削弱|阻碍|反对|拒绝|无效|失败/],
  ] as const;
  const direction = (value: string) => directions.find(([, pattern]) => pattern.test(value))?.[0];
  const leftDirection = direction(left);
  const rightDirection = direction(right);
  return Boolean(leftDirection && rightDirection && leftDirection !== rightDirection);
}

function hasExplicitRebuttal(value: string): boolean {
  return /以偏概全|不能代表|不足以代表|无法代表|不能说明|无法证明|不等于|并非|反驳(?:对方|上述|这一|这个|的是)|不成立|推演到|过度外推|不能外推|哪有|何以见得|无从证明|证据不足|对方(?:说|认为|提到).{0,24}(?:但是|不过|然而|可是|却|但|可(?:线|以|通过|同样))/.test(value);
}

function hasExplicitSourceReference(value: string): boolean {
  return /(?:那份|这份|该项|该|此项|上述|前述|刚才|前面).{0,8}(?:研究|报告|数据|样本|证据|观点|论点|说法|结果)/.test(value);
}

function isEvidenceClaim(value: string): boolean {
  return /(?:研究|报告|调查|数据|实验|统计|样本).{0,24}(?:显示|发现|证明|提升|提高|下降|减少|增长|降低|达到|绩效|效率|结果|百分点|百分之|%)/.test(value)
    || /(?:百分之|%|百分点).{0,8}(?:提升|提高|下降|增长|绩效|效率)/.test(value);
}

type WorkLocationStance = "remote" | "office" | null;

function workLocationStance(value: string): WorkLocationStance {
  if (/观众|投票|举牌|数据|研究|调研|样本|正方主张.{0,30}反方主张|反方主张.{0,30}正方主张/.test(value)) return null;
  if (!/我方|我们认为|我认为|立场|主张|坚持|不可替代|更优解|更值得|根基/.test(value)) return null;
  const remote = /远程(?:办公|工作|工作者|员工|比例|是|为|更)|在家办公|居家办公/.test(value);
  const office = /固定坐班|坐班|办公室|到岗|线下办公/.test(value);
  const remotePositive = /双重胜利|提升|提高|更优|更好|更值得选择|赞成|支持|效率与人本|有利/.test(value);
  const remoteNegative = /权宜之计|不宜|不应|不能|无法|风险|低效|孤独|反对|只是权宜/.test(value);
  const officePositive = /根基|不可替代|更优|更好|赞成|支持|协作效率|执行力|有效/.test(value);
  const officeNegative = /不宜|不应|不能|无法|风险|低效|反对/.test(value);
  if (office && officePositive && !officeNegative) return "office";
  if (remote && remotePositive && !remoteNegative) return "remote";
  if (remote && remoteNegative && office && officePositive && !officeNegative) return "office";
  return null;
}

function repeatedPositionMarker(value: string): boolean {
  return /我方认为|我方坚持|纵观全场|综上|最后交锋|总结|更优解|不可替代/.test(value);
}

type ContentOppositionCue = { signal: "position" | "countermeasure" | "outcome-contrast"; matchingTerms: string[]; score: number };

/** Narrow source-text cues connect debate positions, risk/control pairs, and contrasting outcomes. */
function contentOppositionCue(left: string, right: string): ContentOppositionCue | null {
  const leftStance = workLocationStance(left);
  const rightStance = workLocationStance(right);
  if (leftStance && rightStance && leftStance !== rightStance) {
    const leftFeatures = lexicalFeatures(left).features;
    const rightFeatures = lexicalFeatures(right).features;
    const shared = new Set([...leftFeatures].filter((feature) => rightFeatures.has(feature)
      && !rebuttalStopFeatures.has(feature)));
    const anchors = commonRebuttalAnchors(left, right, shared);
    return {
      signal: "position",
      matchingTerms: anchors.length ? anchors : ["远程办公立场", "固定坐班立场"],
      score: Math.min(0.82, 0.56 + anchors.length * 0.08),
    };
  }

  const hasRisk = (value: string) => /泄露风险|安全风险|风险.{0,12}(?:放大|增加|上升)|隐患/.test(value);
  const hasControls = (value: string) => /vpn|零信任|终端管控|安全体系/i.test(value)
    && /成熟|完善|有效|跑通|防护|降低/.test(value);
  if ((hasRisk(left) && hasControls(right)) || (hasRisk(right) && hasControls(left))) {
    return { signal: "countermeasure", matchingTerms: ["数据或安全风险", "安全防护措施"], score: 0.56 };
  }

  const wellbeing = (value: string): "positive" | "negative" | null => {
    if (/孤岛感|孤立|隔离|脱节|失联|孤独.{0,12}(?:痛点|问题|下降|增加|导致|造成|感到)|(?:痛点|问题).{0,12}孤独/.test(value)) return "negative";
    if (/幸福感|陪伴|健身|满意|快乐|归属感/.test(value)) return "positive";
    return null;
  };
  const leftOutcome = wellbeing(left);
  const rightOutcome = wellbeing(right);
  if (leftOutcome && rightOutcome && leftOutcome !== rightOutcome) {
    return { signal: "outcome-contrast", matchingTerms: ["员工幸福感或陪伴", "孤独或孤岛感"], score: 0.56 };
  }
  return null;
}

const rebuttalStopFeatures = new Set([
  "对方", "我方", "一方", "认为", "中心", "方面", "情况", "只是", "可以",
  "正方", "反方", "方反", "方正", "反驳", "驳有", "有力", "力之", "之处", "处在", "在于",
  "办公", "坐班", "企业", "员工", "团队", "公司", "模式", "管理", "问题",
]);
const rebuttalGenericAnchors = new Set([
  "远程", "远程办公", "办公", "坐班", "企业", "员工", "团队", "公司", "模式", "管理", "协作",
  "本末倒置",
]);

function commonRebuttalAnchors(left: string, right: string, sharedFeatures: ReadonlySet<string>): string[] {
  const anchorsIn = (value: string): string[] => {
    const anchors: string[] = [];
    const content = value.replace(/谢谢主席|纵观全场|我方(?:认为|坚持|主张|从未|也)/g, "");
    for (const token of content.match(/[\u3400-\u9fff]+|[a-z0-9]+/g) ?? []) {
      if (/^[\u3400-\u9fff]+$/.test(token)) {
        let current = "";
        for (let index = 0; index < token.length - 1; index += 1) {
          const bigram = token.slice(index, index + 2);
          if (sharedFeatures.has(bigram)) current = current ? current + token[index + 1] : bigram;
          else if (current) {
            anchors.push(current);
            current = "";
          }
        }
        if (current) anchors.push(current);
      } else if (sharedFeatures.has(token)) anchors.push(token);
    }
    return anchors;
  };
  const cleanAnchor = (value: string) => value
    .replace(/^[的与和及或是这那在到对从]+/u, "")
    .replace(/[的与和及或是这那在到对从]+$/u, "");
  const rightAnchors = new Set(anchorsIn(right).map(cleanAnchor));
  return anchorsIn(left).map(cleanAnchor).filter((anchor, index, all) => anchor.length > 1
    && all.indexOf(anchor) === index && rightAnchors.has(anchor)
    && !rebuttalGenericAnchors.has(anchor));
}

/** High lexical overlap and repeated issue-scoped measures are review hints; no nodes are merged. */
export function findPossibleDuplicates(cells: any[], threshold = 0.78,
                                       metadataById: ReadonlyMap<string, MetadataLike> = new Map(),
                                       limit = 8): PossibleDuplicate[] {
  const board = projectBoard(cells ?? []);
  const candidates = (cells ?? []).filter((cell) => cell.shape !== "edge"
    && ["point", "evidence", "conclusion"].includes(String(cell.data?.type))
    && typeof cell.id === "string" && typeof cell.data?.label === "string")
    .map((cell) => {
      const refs = Array.isArray(cell.data?.metadata_refs) ? cell.data.metadata_refs : [];
      const sourceTexts = refs.flatMap((metaId: unknown) => {
        const record = typeof metaId === "string" ? metadataById.get(metaId) : undefined;
        return typeof record?.text === "string" && record.text.trim() && record.kind !== "summary"
          ? [record.text] : [];
      });
      return { id: cell.id as string, label: String(cell.data.label),
        ...lexicalFeatures(String(cell.data.label)), sourceTexts,
        workStance: sourceTexts.map(workLocationStance).find((stance: WorkLocationStance) => stance !== null) ?? null,
        repeatsPosition: sourceTexts.some(repeatedPositionMarker),
        topicId: nearestIssueId(board, cell.id as string) };
    })
    .filter((cell) => cell.normalized.length > 0);
  const blockedPairs = new Set<string>();
  const pairKey = (left: string, right: string) => [left, right].sort().join("\u0000");
  for (const edge of cells ?? []) {
    if (edge.shape !== "edge" || !["oppose", "duplicate"].includes(String(edge.data?.relation))) continue;
    const left = typeof edge.source === "string" ? edge.source : edge.source?.cell;
    const right = typeof edge.target === "string" ? edge.target : edge.target?.cell;
    if (typeof left === "string" && typeof right === "string") blockedPairs.add(pairKey(left, right));
  }
  const suggestions: PossibleDuplicate[] = [];
  for (let leftIndex = 0; leftIndex < candidates.length; leftIndex += 1) {
    const left = candidates[leftIndex];
    for (let rightIndex = leftIndex + 1; rightIndex < candidates.length; rightIndex += 1) {
      const right = candidates[rightIndex];
      if (blockedPairs.has(pairKey(left.id, right.id))) continue;
      const repeatedPosition = Boolean(left.topicId === right.topicId && left.workStance
        && left.workStance === right.workStance && left.repeatsPosition && right.repeatsPosition);
      if (oppositePolarity(left.normalized, right.normalized) && !repeatedPosition) continue;
      if (repeatedPosition) {
        const diceScore = (leftFeatures: Set<string>, rightFeatures: Set<string>) => {
          const shared = [...leftFeatures].filter((feature) => rightFeatures.has(feature));
          return shared.length ? (2 * shared.length) / (leftFeatures.size + rightFeatures.size) : 0;
        };
        let contentOverlap = diceScore(left.features, right.features);
        for (const leftText of left.sourceTexts) for (const rightText of right.sourceTexts) {
          contentOverlap = Math.max(contentOverlap,
            diceScore(lexicalFeatures(leftText).features, lexicalFeatures(rightText).features));
        }
        suggestions.push({ leftId: left.id, leftLabel: left.label,
          rightId: right.id, rightLabel: right.label, scoreBasis: "source", signal: "repeated-position",
          matchingTerms: [left.workStance === "office" ? "固定坐班立场" : "远程办公立场"],
          score: Math.round((0.5 + 0.49 * contentOverlap) * 1000) / 1000 });
        continue;
      }
      const labelTerms = [...left.features].filter((feature) => right.features.has(feature));
      const labelScore = left.normalized === right.normalized ? 1
        : labelTerms.length === 0 ? 0 : (2 * labelTerms.length) / (left.features.size + right.features.size);
      let sourceScore = 0;
      let sourceTerms: string[] = [];
      let contradictorySourceMatch = false;
      let sharedMeasureTerms: string[] = [];
      for (const leftText of left.sourceTexts) for (const rightText of right.sourceTexts) {
        const leftSource = lexicalFeatures(leftText);
        const rightSource = lexicalFeatures(rightText);
        const shared = [...leftSource.features].filter((feature) => rightSource.features.has(feature));
        const score = leftSource.normalized === rightSource.normalized ? 1
          : shared.length === 0 ? 0 : (2 * shared.length) / (leftSource.features.size + rightSource.features.size);
        if (oppositePolarity(leftSource.normalized, rightSource.normalized) && score >= threshold) {
          contradictorySourceMatch = true;
          break;
        }
        if (score > sourceScore) { sourceScore = score; sourceTerms = shared; }
        if (left.topicId && left.topicId === right.topicId && !oppositePolarity(leftSource.normalized, rightSource.normalized)) {
          const sharedMeasures = [...measureAnchors(leftText)].filter((anchor) =>
            measureAnchors(rightText).has(anchor) && !isGenericScheduleMeasure(anchor));
          const distinctiveShared = shared.filter((feature) =>
            !sharedMeasureStopFeatures.has(feature) && !genericCountFeature(feature));
          if (sharedMeasures.length && distinctiveShared.length >= 1 && sharedMeasures.length + distinctiveShared.length > sharedMeasureTerms.length) {
            sharedMeasureTerms = [...distinctiveShared, ...sharedMeasures];
          }
        }
      }
      if (contradictorySourceMatch) continue;
      const score = Math.max(labelScore, sourceScore);
      const signal = sharedMeasureTerms.length ? "shared-measure" : "similar-wording";
      const scoreBasis = signal === "shared-measure" || sourceScore > labelScore ? "source" : "label";
      const matchingTerms = signal === "shared-measure" ? sharedMeasureTerms
        : scoreBasis === "source" ? sourceTerms : labelTerms;
      if (score < threshold && signal !== "shared-measure") continue;
      if (signal !== "shared-measure" && scoreBasis === "label" && left.normalized !== right.normalized && labelTerms.length < 2) continue;
      suggestions.push({ leftId: left.id, leftLabel: left.label,
        rightId: right.id, rightLabel: right.label, scoreBasis, signal, matchingTerms: matchingTerms.slice(0, 8),
        score: Math.round(score * 1000) / 1000 });
    }
  }
  return suggestions.sort((left, right) => Number(right.signal === "shared-measure") - Number(left.signal === "shared-measure")
    || right.score - left.score
    || left.leftId.localeCompare(right.leftId) || left.rightId.localeCompare(right.rightId))
    .slice(0, Math.max(0, Math.min(Math.floor(limit), 100)));
}

export interface PossibleOpposition {
  leftId: string;
  leftLabel: string;
  rightId: string;
  rightLabel: string;
  topicId: string;
  topicLabel: string;
  leftTopicLabel: string;
  rightTopicLabel: string;
  scopeMismatch: boolean;
  scoreBasis: "label" | "source";
  signal: "polarity" | "explicit-rebuttal" | "position" | "countermeasure" | "outcome-contrast";
  matchingTerms: string[];
  score: number;
}

/** Opposing wording becomes a review hint, never an automatic relation. */
export function findPossibleOppositions(cells: any[], threshold = 0.55,
                                        metadataById: ReadonlyMap<string, MetadataLike> = new Map(),
                                        limit = 20): PossibleOpposition[] {
  const board = projectBoard(cells ?? []);
  const candidates = (cells ?? []).filter((cell) => cell.shape !== "edge"
    && ["point", "conclusion", "evidence"].includes(String(cell.data?.type))
    && typeof cell.id === "string" && typeof cell.data?.label === "string")
    .map((cell) => {
      const seen = new Set<string>();
      let parentId = board.parentById.get(cell.id);
      let topicId = "";
      while (parentId && !seen.has(parentId)) {
        seen.add(parentId);
        const parent = board.nodeById.get(parentId);
        if (!parent) break;
        if (parent.data.type === "issue") {
          topicId = parent.id === "n_issue_root" ? "" : parent.id;
          break;
        }
        parentId = board.parentById.get(parentId);
      }
      const sourceTexts = refs(cell.data).flatMap((metaId) => {
        const record = metadataById.get(metaId);
        return typeof record?.text === "string" && record.text.trim() && record.kind !== "summary"
          ? [record.text] : [];
      });
      return { id: cell.id as string, label: String(cell.data.label),
        ...lexicalFeatures(String(cell.data.label)), sourceTexts, topicId,
        topicLabel: topicId ? String(board.nodeById.get(topicId)?.data.label ?? topicId) : "未分配议题" };
    }).filter((cell) => cell.normalized.length > 0);
  const blockedPairs = new Set<string>();
  const pairKey = (left: string, right: string) => [left, right].sort().join("\u0000");
  for (const edge of cells ?? []) {
    if (edge.shape !== "edge" || !["oppose", "duplicate"].includes(String(edge.data?.relation))) continue;
    const left = typeof edge.source === "string" ? edge.source : edge.source?.cell;
    const right = typeof edge.target === "string" ? edge.target : edge.target?.cell;
    if (typeof left === "string" && typeof right === "string") blockedPairs.add(pairKey(left, right));
  }
  const suggestions: PossibleOpposition[] = [];
  for (let leftIndex = 0; leftIndex < candidates.length; leftIndex += 1) {
    const left = candidates[leftIndex];
    for (let rightIndex = leftIndex + 1; rightIndex < candidates.length; rightIndex += 1) {
      const right = candidates[rightIndex];
      if (blockedPairs.has(pairKey(left.id, right.id))) continue;
      const sameTopic = left.topicId === right.topicId;
      const variants = (candidate: typeof left) => [
        { ...lexicalFeatures(candidate.label), basis: "label" as const },
        ...candidate.sourceTexts.map((text) => ({ ...lexicalFeatures(text), basis: "source" as const })),
      ];
      let best: { score: number; matchingTerms: string[]; basis: "label" | "source"; signal: PossibleOpposition["signal"] } | null = null;
      for (const leftText of variants(left)) for (const rightText of variants(right)) {
        const cue = sameTopic && leftText.basis === "source" && rightText.basis === "source"
          ? contentOppositionCue(leftText.normalized, rightText.normalized) : null;
        if (cue) {
          const priority: Record<PossibleOpposition["signal"], number> = {
            "explicit-rebuttal": 4, position: 3, countermeasure: 2, "outcome-contrast": 2, polarity: 1,
          };
          if (!best || priority[cue.signal] > priority[best.signal]) {
            best = { score: cue.score, matchingTerms: cue.matchingTerms, basis: "source", signal: cue.signal };
          }
        }
        const polarityConflict = sameTopic && oppositePolarity(leftText.normalized, rightText.normalized);
        const leftIsRebuttal = leftText.basis === "source" && hasExplicitRebuttal(leftText.normalized);
        const rightIsRebuttal = rightText.basis === "source" && hasExplicitRebuttal(rightText.normalized);
        const explicitRebuttal = leftIsRebuttal || rightIsRebuttal;
        if (!sameTopic && !explicitRebuttal) continue;
        if (!polarityConflict && !explicitRebuttal) continue;
        const sharedTerms = [...leftText.features].filter((feature) => rightText.features.has(feature));
        const matchingTerms = explicitRebuttal
          ? commonRebuttalAnchors(leftText.normalized, rightText.normalized,
            new Set(sharedTerms.filter((feature) => !rebuttalStopFeatures.has(feature))))
          : sharedTerms;
        const anchoredEvidenceReference = (leftText.basis === "source"
          && hasExplicitSourceReference(leftText.normalized)
          && rightText.basis === "source" && isEvidenceClaim(rightText.normalized))
          || (rightText.basis === "source" && hasExplicitSourceReference(rightText.normalized)
            && leftText.basis === "source" && isEvidenceClaim(leftText.normalized));
        const anchoredSingleRebuttal = anchoredEvidenceReference
          && !(leftIsRebuttal && rightIsRebuttal);
        const minimumTerms = explicitRebuttal ? (anchoredSingleRebuttal ? 1 : 2) : 2;
        if (matchingTerms.length < minimumTerms) continue;
        const lexicalScore = (2 * matchingTerms.length) / (leftText.features.size + rightText.features.size || 1);
        const score = anchoredSingleRebuttal ? Math.max(0.82, lexicalScore) : lexicalScore;
        if (!explicitRebuttal && score < threshold) continue;
        const basis = leftText.basis === "source" || rightText.basis === "source" ? "source" : "label";
        const signal = explicitRebuttal ? "explicit-rebuttal" : "polarity";
        const priority: Record<PossibleOpposition["signal"], number> = {
          "explicit-rebuttal": 4, position: 3, countermeasure: 2, "outcome-contrast": 2, polarity: 1,
        };
        if (!best || priority[signal] > priority[best.signal]
          || (signal === best.signal && score > best.score)) best = { score, matchingTerms, basis, signal };
      }
      if (!best) continue;
      suggestions.push({ leftId: left.id, leftLabel: left.label,
        rightId: right.id, rightLabel: right.label,
        topicId: sameTopic ? left.topicId : "",
        topicLabel: sameTopic ? left.topicLabel : "跨议题候选",
        leftTopicLabel: left.topicLabel, rightTopicLabel: right.topicLabel, scopeMismatch: !sameTopic,
        signal: best.signal,
        scoreBasis: best.basis, matchingTerms: best.matchingTerms.slice(0, 8),
        score: Math.round(best.score * 1000) / 1000 });
    }
  }
  const priority: Record<PossibleOpposition["signal"], number> = {
    "explicit-rebuttal": 4, position: 3, countermeasure: 2, "outcome-contrast": 2, polarity: 1,
  };
  const bySignal = new Map<PossibleOpposition["signal"], PossibleOpposition[]>(
    Object.keys(priority).map((signal) => [signal as PossibleOpposition["signal"], []]),
  );
  for (const suggestion of suggestions) bySignal.get(suggestion.signal)?.push(suggestion);
  for (const group of bySignal.values()) {
    group.sort((left, right) => right.score - left.score
      || left.leftId.localeCompare(right.leftId) || left.rightId.localeCompare(right.rightId));
  }
  const signalOrder = (Object.keys(priority) as PossibleOpposition["signal"][])
    .sort((left, right) => priority[right] - priority[left]);
  const maxResults = Math.max(0, Math.trunc(limit));
  const ranked: PossibleOpposition[] = [];
  for (let round = 0; ranked.length < maxResults; round += 1) {
    let found = false;
    for (const signal of signalOrder) {
      const candidate = bySignal.get(signal)?.[round];
      if (!candidate) continue;
      ranked.push(candidate);
      found = true;
      if (ranked.length >= maxResults) break;
    }
    if (!found) break;
  }
  return ranked;
}
