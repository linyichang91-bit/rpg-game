"use client";

import { useDeferredValue, useEffect, useState } from "react";

type TypewriterTextProps = {
  text: string;
  animate?: boolean;
  className?: string;
  streaming?: boolean;
};

export function TypewriterText({
  text,
  animate = false,
  className,
  streaming = false
}: TypewriterTextProps) {
  const deferredText = useDeferredValue(text);
  const displayText = streaming ? text : deferredText;
  const paragraphs = splitNarrativeParagraphs(displayText);
  const [visibleParagraphCount, setVisibleParagraphCount] = useState(
    streaming || !animate ? paragraphs.length : 0
  );

  useEffect(() => {
    if (streaming || !animate) {
      setVisibleParagraphCount(paragraphs.length);
      return;
    }

    setVisibleParagraphCount(0);
    const paragraphDelayMs = paragraphs.length >= 8 ? 90 : 130;
    const timer = window.setInterval(() => {
      setVisibleParagraphCount((previous) => {
        if (previous >= paragraphs.length) {
          window.clearInterval(timer);
          return previous;
        }

        return previous + 1;
      });
    }, paragraphDelayMs);

    return () => window.clearInterval(timer);
  }, [animate, paragraphs.length, streaming, displayText]);

  const visibleParagraphs = streaming
    ? paragraphs
    : paragraphs.slice(0, visibleParagraphCount);

  return (
    <div className={className}>
      {visibleParagraphs.map((paragraph, index) => (
        <p className="story-paragraph" key={`${index}-${paragraph.slice(0, 24)}`}>
          {paragraph}
        </p>
      ))}
      {(streaming || (animate && visibleParagraphCount < paragraphs.length)) ? (
        <span className="typewriter-caret" aria-hidden="true" />
      ) : null}
    </div>
  );
}

function splitNarrativeParagraphs(text: string): string[] {
  const normalized = text.replace(/\r\n/g, "\n").trim();
  if (!normalized) {
    return [];
  }

  const parts = normalized
    .split(/\n{2,}/)
    .map((paragraph) => paragraph.trim())
    .filter(Boolean);

  if (parts.length > 0) {
    return parts;
  }

  return [normalized];
}
