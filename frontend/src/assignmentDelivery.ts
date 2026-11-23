export function assignmentDeliveryMessage(state: string | null | undefined) {
  switch (state) {
    case "skipped":
      return "应用消息未发送，责任人可登录系统查看任务；原因可在通知记录中查看。";
    case "pending":
      return "应用消息排队发送中，责任人可在系统查看任务。";
    case "sending":
      return "应用消息正在发送，责任人可在系统查看任务。";
    case "sent":
      return "应用消息已发送，责任人可在企业微信或系统中查看任务。";
    case "failed":
    case "blocked":
      return "应用消息发送失败，请管理员查看通知记录；责任人仍可登录系统查看任务。";
    default:
      return "应用消息送达状态待核实，责任人可登录系统查看任务。";
  }
}
