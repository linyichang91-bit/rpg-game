"use client";

import { useDeferredValue, useEffect, useState } from "react";

type TypewriterTextProps = {
  text: string;
  animate?: boolean;
  className?: string;
};

export function TypewriterText({
  text,
  animate = false,
  className
}: TypewriterTextProps) {
  const deferredText = useDeferredValue(text);
  const paragraphs = splitNarrativeParagraphs(deferredText);
  const [visibleParagraphCount, setVisibleParagraphCount] = useState(
    animate ? 0 : paragraphs.length
  );

  useEffect(() => {
    if (!animate) {
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
  }, [animate, deferredText, paragraphs.length]);

  const visibleParagraphs = paragraphs.slice(0, visibleParagraphCount);

  return (
    <div className={className}>
      {visibleParagraphs.map((paragraph, index) => (
        <p className="story-paragraph" key={`${index}-${paragraph.slice(0, 24)}`}>
          {paragraph}
        </p>
      ))}
      {animate && visibleParagraphCount < paragraphs.length ? (
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
