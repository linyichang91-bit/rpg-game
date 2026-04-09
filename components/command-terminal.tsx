"use client";

import { FormEvent, useDeferredValue, useState } from "react";

type CommandTerminalProps = {
  isLoading: boolean;
  onSubmit: (command: string) => Promise<void>;
};

export function CommandTerminal({
  isLoading,
  onSubmit
}: CommandTerminalProps) {
  const [command, setCommand] = useState("");
  const deferredCommand = useDeferredValue(command);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = command.trim();
    if (!trimmed || isLoading) {
      return;
    }

    setCommand("");
    await onSubmit(trimmed);
  }

  return (
    <form className="terminal-form" onSubmit={handleSubmit}>
      <label className="terminal-label" htmlFor="command-input">
        指令终端
      </label>
      <div className="terminal-shell">
        <span className="terminal-prefix">$</span>
        <input
          id="command-input"
          autoComplete="off"
          className="terminal-input"
          disabled={isLoading}
          onChange={(event) => setCommand(event.target.value)}
          placeholder="用自然语言描述你的行动……"
          value={command}
        />
        <button
          className="terminal-submit"
          disabled={!deferredCommand.trim() || isLoading}
          type="submit"
        >
          {isLoading ? "结算中……" : "发送"}
        </button>
      </div>
    </form>
  );
}
