import { afterEach, describe, expect, it, vi } from "vitest";
import {
  AUTH_EXPIRED_EVENT,
  getAuthToken,
  setAuthToken,
  StreamChatError,
  streamChat,
} from "./client";

function sseResponse(body: string, status = 200): Response {
  const encoded = new TextEncoder().encode(body);
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(encoded);
      controller.close();
    },
  });
  return new Response(stream, {
    status,
    headers: { "Content-Type": "text/event-stream" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("streamChat", () => {
  it("parses replacement and validated completion events", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      sseResponse(
        [
          'id: 1\nevent: meta\ndata: {"conversation_id":"conv-1","request_id":"req-12345678","used_query":"q","diagnostics":{"total_ms":2.1}}\n\n',
          'id: 2\nevent: token\ndata: {"text":"draft"}\n\n',
          'id: 3\nevent: replace\ndata: {"answer":"final [1]","sources":[]}\n\n',
          'id: 4\nevent: done\ndata: {"message_id":"m1","conversation_id":"conv-1","request_id":"req-12345678","answer":"final [1]","sources":[],"diagnostics":{"total_ms":2.1}}\n\n',
        ].join(""),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const tokens: string[] = [];
    const replacements: string[] = [];
    const completions: string[] = [];

    await streamChat(
      {
        kb_id: "kb-1",
        question: "q",
        request_id: "req-12345678",
      },
      {
        onToken: (text) => tokens.push(text),
        onReplace: (data) => replacements.push(data.answer),
        onDone: (data) => completions.push(data.answer),
      },
    );

    expect(tokens).toEqual(["draft"]);
    expect(replacements).toEqual(["final [1]"]);
    expect(completions).toEqual(["final [1]"]);
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toMatchObject({
      request_id: "req-12345678",
      stream: true,
    });
  });

  it("rejects a stream that ends without done", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(sseResponse('event: token\ndata: {"text":"partial"}\n\n')),
    );
    await expect(
      streamChat(
        { kb_id: "kb-1", question: "q", request_id: "req-12345678" },
        {},
      ),
    ).rejects.toMatchObject({
      name: "StreamChatError",
      retryable: true,
    });
  });

  it("does not mark ordinary 4xx responses as retryable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "参数错误" }), {
          status: 400,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    try {
      await streamChat(
        { kb_id: "kb-1", question: "q", request_id: "req-12345678" },
        {},
      );
      throw new Error("expected streamChat to reject");
    } catch (error) {
      expect(error).toBeInstanceOf(StreamChatError);
      expect(error).toMatchObject({ status: 400, retryable: false, message: "参数错误" });
    }
  });

  it("clears an expired token and notifies the application on 401", async () => {
    setAuthToken("expired-token");
    const listener = vi.fn();
    window.addEventListener(AUTH_EXPIRED_EVENT, listener);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "登录已失效" }), {
          status: 401,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    await expect(
      streamChat(
        { kb_id: "kb-1", question: "q", request_id: "req-12345678" },
        {},
      ),
    ).rejects.toMatchObject({ status: 401, retryable: false });
    expect(getAuthToken()).toBe("");
    expect(listener).toHaveBeenCalledTimes(1);
    window.removeEventListener(AUTH_EXPIRED_EVENT, listener);
  });

  it("preserves AbortError so the UI can treat stop as intentional", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new DOMException("aborted", "AbortError")),
    );
    await expect(
      streamChat(
        { kb_id: "kb-1", question: "q", request_id: "req-12345678" },
        {},
      ),
    ).rejects.toMatchObject({ name: "AbortError" });
  });
});
