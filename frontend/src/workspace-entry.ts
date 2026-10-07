import { meetingsApi } from "./api/meetings";

const meetingId = new URLSearchParams(location.search).get("meeting_id")?.trim();

if (meetingId) {
  void import("./main");
} else {
  const message = document.getElementById("meeting-message");
  const create = async () => {
    if (message) message.textContent = "正在创建会议…";
    try {
      const result = await meetingsApi.create("新会议");
      location.replace(`/workspace.html?meeting_id=${encodeURIComponent(result.meeting_id)}`);
    } catch {
      if (message) message.textContent = "创建会议失败，请确认后端已启动后重试。";
      const retry = document.createElement("button");
      retry.type = "button";
      retry.textContent = "重试创建";
      retry.addEventListener("click", () => { retry.remove(); void create(); });
      message?.append(retry);
    }
  };
  void create();
}
