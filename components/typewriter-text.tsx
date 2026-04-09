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
  const [visibleCount, setVisibleCount] = useState(
    animate ? 0 : deferredText.length
  );

  useEffect(() => {
    if (!animate) {
      setVisibleCount(deferredText.length);
      return;
    }

    setVisibleCount(0);
    const timer = window.setInterval(() => {
      setVisibleCount((previous) => {
        if (previous >= deferredText.length) {
          window.clearInterval(timer);
          return previous;
        }
        return previous + 1;
      });
    }, 12);

    return () => window.clearInterval(timer);
  }, [animate, deferredText]);

  const visibleText = deferredText.slice(0, visibleCount);

  return (
    <span className={className}>
      {visibleText}
      {animate && visibleCount < deferredText.length ? (
        <span className="typewriter-caret" aria-hidden="true" />
      ) : null}
    </span>
  );
}
