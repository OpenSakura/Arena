// @vitest-environment jsdom

import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TurnstileWidget } from "./TurnstileWidget";

type TurnstileOptions = Record<string, unknown>;

type TurnstileApi = {
  render: (container: HTMLElement, options: TurnstileOptions) => string;
  reset: (widgetId?: string) => void;
  remove?: (widgetId: string) => void;
};

type ScriptProps = {
  id?: string;
  src?: string;
  strategy?: string;
  onLoad?: () => void;
};

vi.mock("next/script", () => ({
  default: ({ onLoad }: ScriptProps) => {
    return (
      <button type="button" data-testid="turnstile-script" onClick={() => onLoad?.()}>
        load-turnstile
      </button>
    );
  },
}));

function setTurnstile(api: TurnstileApi | undefined) {
  (window as Window & { turnstile?: TurnstileApi }).turnstile = api;
}

afterEach(() => {
  vi.restoreAllMocks();
  setTurnstile(undefined);
});

beforeEach(() => {
  setTurnstile(undefined);
});

describe("TurnstileWidget", () => {
  it("shows loading state before Turnstile script is ready", () => {
    render(<TurnstileWidget siteKey="site-key" onToken={vi.fn()} />);

    expect(screen.getByText("Loading Turnstile...")).toBeDefined();
  });

  it("renders widget with options and forwards widget callbacks", async () => {
    const onToken = vi.fn();
    const onExpire = vi.fn();
    const onError = vi.fn();

    const renderMock = vi.fn<(container: HTMLElement, options: TurnstileOptions) => string>();
    renderMock.mockReturnValue("widget-1");

    setTurnstile({
      render: renderMock,
      reset: vi.fn(),
      remove: vi.fn(),
    });

    render(
      <TurnstileWidget
        siteKey="site-key"
        theme="dark"
        action="vote"
        cData="battle-1"
        onToken={onToken}
        onExpire={onExpire}
        onError={onError}
      />,
    );

    await waitFor(() => {
      expect(renderMock).toHaveBeenCalledTimes(1);
    });

    const [, options] = renderMock.mock.calls[0] as [HTMLElement, TurnstileOptions];
    expect(options.sitekey).toBe("site-key");
    expect(options.theme).toBe("dark");
    expect(options.action).toBe("vote");
    expect(options.cData).toBe("battle-1");

    const callback = options.callback as ((token: unknown) => void) | undefined;
    const expiredCallback = options["expired-callback"] as (() => void) | undefined;
    const timeoutCallback = options["timeout-callback"] as (() => void) | undefined;
    const errorCallback = options["error-callback"] as (() => void) | undefined;

    callback?.("token-123");
    expiredCallback?.();
    timeoutCallback?.();
    errorCallback?.();

    expect(onToken).toHaveBeenCalledWith("token-123");
    expect(onExpire).toHaveBeenCalledTimes(2);
    expect(onError).toHaveBeenCalledWith("Turnstile error");
  });

  it("removes old widget before re-rendering with new props", async () => {
    const renderMock = vi.fn<(container: HTMLElement, options: TurnstileOptions) => string>();
    renderMock.mockReturnValueOnce("widget-1").mockReturnValueOnce("widget-2");
    const removeMock = vi.fn();

    setTurnstile({
      render: renderMock,
      reset: vi.fn(),
      remove: removeMock,
    });

    const { rerender } = render(<TurnstileWidget siteKey="site-key" action="vote-a" onToken={vi.fn()} />);

    await waitFor(() => {
      expect(renderMock).toHaveBeenCalledTimes(1);
    });

    rerender(<TurnstileWidget siteKey="site-key" action="vote-b" onToken={vi.fn()} />);

    await waitFor(() => {
      expect(renderMock).toHaveBeenCalledTimes(2);
    });

    expect(removeMock).toHaveBeenCalledWith("widget-1");
  });

  it("cleans up widget instance on unmount", async () => {
    const renderMock = vi.fn<(container: HTMLElement, options: TurnstileOptions) => string>();
    renderMock.mockReturnValue("widget-cleanup");
    const removeMock = vi.fn();

    setTurnstile({
      render: renderMock,
      reset: vi.fn(),
      remove: removeMock,
    });

    const { unmount } = render(<TurnstileWidget siteKey="site-key" onToken={vi.fn()} />);

    await waitFor(() => {
      expect(renderMock).toHaveBeenCalledTimes(1);
    });

    unmount();

    expect(removeMock).toHaveBeenCalledWith("widget-cleanup");
  });
});
