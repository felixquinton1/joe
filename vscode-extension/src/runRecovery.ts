import {
  JoeActiveRun,
  JoeConversationDetail,
  JoeRunEvent,
} from "./api";

export const MAX_PERSISTED_STREAM_CHARACTERS = 100_000;

export interface JoeRunBookmark {
  runId: string;
  conversationId: string;
  request: string;
  lastEventId: number;
  streamedText: string;
  streamTruncated?: boolean;
}

export function chooseRunToResume(
  activeRuns: JoeActiveRun[],
  bookmark: JoeRunBookmark | undefined,
  selectedConversationId: string | undefined
): JoeRunBookmark | undefined {
  if (bookmark) {
    return bookmark;
  }
  const active =
    activeRuns.find(run => run.conversation_id === selectedConversationId) ??
    activeRuns[0];
  if (!active) {
    return undefined;
  }
  return {
    runId: active.run_id,
    conversationId: active.conversation_id,
    request: active.request,
    lastEventId: 0,
    streamedText: "",
  };
}

export function updateBookmark(
  bookmark: JoeRunBookmark,
  event: JoeRunEvent,
  streamedText: string
): JoeRunBookmark {
  const truncated =
    streamedText.length > MAX_PERSISTED_STREAM_CHARACTERS;
  return {
    ...bookmark,
    lastEventId: Math.max(bookmark.lastEventId, event.event_id || 0),
    streamedText: truncated
      ? streamedText.slice(-MAX_PERSISTED_STREAM_CHARACTERS)
      : streamedText,
    streamTruncated: bookmark.streamTruncated || truncated || undefined,
  };
}

export function completedResponse(
  conversation: JoeConversationDetail,
  runId: string
): string | undefined {
  return [...(conversation.messages ?? [])]
    .reverse()
    .find(message => message.role === "assistant" && message.run_id === runId)
    ?.content;
}
