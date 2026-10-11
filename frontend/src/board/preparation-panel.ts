import { meetingsApi, type PreparationMaterial, type PreparationQuestion,
  type PreparationSuggestion } from "../api/meetings";

const questionStatus = { pending: "待解决", resolved: "已解决", dismissed: "已搁置" };

function readBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1] ?? "");
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

export function mountPreparationPanel(host: HTMLElement, meetingId: string) {
  let materials: PreparationMaterial[] = [];
  let suggestions: PreparationSuggestion[] = [];
  let questions: PreparationQuestion[] = [];
  let revision = 0;

  function render(message = "") {
    host.replaceChildren();
    const upload = document.createElement("div");
    upload.className = "prep-upload";
    const fileLabel = document.createElement("label");
    fileLabel.textContent = "会前材料（TXT / Markdown / 文本 PDF）";
    const fileInput = document.createElement("input");
    fileInput.type = "file";
    fileInput.accept = ".txt,.md,.pdf,text/plain,text/markdown,application/pdf";
    fileInput.className = "prep-material-file";
    fileLabel.append(fileInput);
    const uploadButton = document.createElement("button");
    uploadButton.type = "button";
    uploadButton.textContent = "导入材料";
    uploadButton.addEventListener("click", async () => {
      const file = fileInput.files?.[0];
      if (!file) { render("请先选择文件"); return; }
      uploadButton.disabled = true;
      try {
        const content = await readBase64(file);
        await meetingsApi.uploadMaterial(meetingId, file.name, content);
        await update();
      } catch {
        render("材料导入失败；请检查格式、大小或 PDF 是否含可选中文字");
      }
    });
    upload.append(fileLabel, uploadButton);
    host.append(upload);
    if (materials.length) {
      const materialList = document.createElement("p");
      materialList.className = "prep-sources";
      materialList.textContent = `已导入：${materials.map((item) => item.filename).join("、")}`;
      host.append(materialList);
    }

    const candidateHeader = document.createElement("h3");
    candidateHeader.textContent = "待决问题候选（需人工确认）";
    host.append(candidateHeader);
    const accepted = new Set(questions.map((item) => item.source_ref));
    const remaining = suggestions.filter((item) => !accepted.has(item.source_ref));
    if (!remaining.length) {
      const empty = document.createElement("p");
      empty.className = "insight-empty";
      empty.textContent = "尚无新的待决问题候选。";
      host.append(empty);
    }
    for (const suggestion of remaining) {
      const card = document.createElement("div");
      card.className = "prep-candidate";
      const source = document.createElement("small");
      source.textContent = suggestion.source_label;
      const field = document.createElement("input");
      field.type = "text";
      field.maxLength = 200;
      field.value = suggestion.text;
      field.setAttribute("aria-label", `编辑待决问题：${suggestion.source_label}`);
      const confirm = document.createElement("button");
      confirm.type = "button";
      confirm.className = "prep-confirm";
      confirm.textContent = "确认问题";
      confirm.addEventListener("click", async () => {
        confirm.disabled = true;
        try {
          const result = await meetingsApi.addQuestion(meetingId,
            `prep:${suggestion.source_ref}`, field.value.trim(),
            suggestion.source_kind, suggestion.source_ref);
          questions = [...questions, result.question];
          render("问题已加入清单");
        } catch {
          confirm.disabled = false;
          confirm.textContent = "确认失败，请核对后重试";
        }
      });
      card.append(source, field, confirm);
      host.append(card);
    }

    const manual = document.createElement("div");
    manual.className = "prep-manual";
    const manualInput = document.createElement("input");
    manualInput.type = "text";
    manualInput.maxLength = 200;
    manualInput.placeholder = "补充一个待决问题";
    manualInput.setAttribute("aria-label", "补充待决问题");
    const manualButton = document.createElement("button");
    manualButton.type = "button";
    manualButton.textContent = "添加问题";
    manualButton.addEventListener("click", async () => {
      if (!manualInput.value.trim()) return;
      manualButton.disabled = true;
      try {
        const result = await meetingsApi.addQuestion(meetingId,
          `manual:${crypto.randomUUID()}`, manualInput.value.trim(), "manual", null);
        questions = [...questions, result.question];
        render("问题已加入清单");
      } catch {
        manualButton.disabled = false;
        manualButton.textContent = "添加失败，请重试";
      }
    });
    manual.append(manualInput, manualButton);
    host.append(manual);

    const listHeader = document.createElement("h3");
    listHeader.textContent = `问题清单 ${questions.length}`;
    host.append(listHeader);
    for (const question of questions) {
      const row = document.createElement("div");
      row.className = "prep-question";
      const text = document.createElement("strong");
      text.textContent = question.text;
      const status = document.createElement("span");
      status.textContent = questionStatus[question.status];
      const source = document.createElement("small");
      const material = materials.find((item) => question.source_ref?.startsWith(`${item.id}:`));
      source.textContent = question.source_kind === "manual" ? "人工补充"
        : material ? `${material.filename} · ${question.source_ref?.slice(material.id.length + 1)}`
        : question.source_kind === "agenda" ? `议程第 ${question.source_ref?.split(":")[1]} 项`
        : question.source_ref ?? "来源待核对";
      row.append(text, status, source);
      if (question.source_kind === "material" && question.source_ref) {
        const viewSource = document.createElement("button");
        viewSource.type = "button";
        viewSource.textContent = "查看材料原文";
        viewSource.addEventListener("click", async () => {
          viewSource.disabled = true;
          try {
            const result = await meetingsApi.materialSource(meetingId, question.source_ref!);
            let excerpt = row.querySelector<HTMLElement>(".prep-excerpt");
            if (!excerpt) {
              excerpt = document.createElement("p");
              excerpt.className = "prep-excerpt";
              row.append(excerpt);
            }
            excerpt.textContent = result.text;
          } catch {
            viewSource.textContent = "原文读取失败，请重试";
          } finally {
            viewSource.disabled = false;
          }
        });
        row.append(viewSource);
      }
      if (question.status === "pending") {
        for (const [next, label, cls] of [["resolved", "标记已解决", "prep-resolve"],
          ["dismissed", "暂缓", "prep-dismiss"]] as const) {
          const button = document.createElement("button");
          button.type = "button";
          button.className = cls;
          button.textContent = label;
          button.addEventListener("click", async () => {
            button.disabled = true;
            try {
              const result = await meetingsApi.updateQuestion(meetingId, question.id,
                question.version, next);
              questions = questions.map((item) => item.id === question.id ? result.question : item);
              render("问题状态已更新");
            } catch {
              button.disabled = false;
              button.textContent = "更新失败，请刷新重试";
            }
          });
          row.append(button);
        }
      }
      host.append(row);
    }
    if (message) {
      const feedback = document.createElement("p");
      feedback.setAttribute("role", "status");
      feedback.textContent = message;
      host.prepend(feedback);
    }
  }

  async function update() {
    const current = ++revision;
    try {
      const result = await meetingsApi.preparation(meetingId);
      if (current !== revision) return;
      materials = result.materials;
      suggestions = result.suggestions;
      questions = result.questions;
      render();
    } catch {
      if (current === revision) host.textContent = "会前准备暂不可用，请刷新重试。";
    }
  }

  return { update };
}
