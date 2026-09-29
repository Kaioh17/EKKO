import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@tauri-apps/api/core", () => ({
  isTauri: () => true,
  invoke: vi.fn(async () => ({ url: "http://127.0.0.1:5555", token: "tok" })),
}));

beforeEach(() => {
  vi.resetModules();
});

describe("client", () => {
  it("uses the url and token from the Tauri backend_info command", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: 1 }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const { getSettings } = await import("./client");
    await getSettings("llm");
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("http://127.0.0.1:5555/api/settings/llm");
    expect((init.headers as Record<string, string>)["X-Ekko-Token"]).toBe("tok");
    expect(url).not.toContain("tok");
  });
});

describe("status socket", () => {
  it("authenticates with the ekko subprotocol, never the URL", async () => {
    const made: { url: string; protocols: string[]; ws: { onmessage?: (e: { data: string }) => void } }[] = [];
    class FakeWS {
      onmessage?: (e: { data: string }) => void;
      onclose?: () => void;
      onerror?: () => void;
      constructor(url: string, protocols: string[]) {
        made.push({ url, protocols, ws: this });
      }
      close() {}
    }
    vi.stubGlobal("WebSocket", FakeWS);
    const { subscribe } = await import("./socket");
    const got: unknown[] = [];
    subscribe((m) => got.push(m));
    subscribe(() => {});
    await vi.waitFor(() => expect(made.length).toBe(1)); // one shared socket
    expect(made[0].url).toBe("ws://127.0.0.1:5555/ws/status");
    expect(made[0].protocols).toEqual(["ekko", "tok"]);
    made[0].ws.onmessage?.({ data: JSON.stringify({ type: "status", state: "listening" }) });
    made[0].ws.onmessage?.({ data: "not json" });
    expect(got).toEqual([{ type: "status", state: "listening" }]);
  });
});

describe("safeUrl", () => {
  it("only lets http(s) through", async () => {
    const { safeUrl } = await import("./safeUrl");
    expect(safeUrl("https://example.com/a")).toBe("https://example.com/a");
    expect(safeUrl("http://example.com")).toBe("http://example.com/");
    expect(safeUrl("javascript:alert(1)")).toBeNull();
    expect(safeUrl("file:///c:/x")).toBeNull();
    expect(safeUrl("not a url")).toBeNull();
  });
});
