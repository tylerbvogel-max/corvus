import { useEffect } from 'react';

type Identifiable = { id: number | string };

interface Options<T extends Identifiable> {
  items: T[];
  selectedId: T['id'] | null | undefined;
  onSelect: (id: T['id']) => void;
  enabled?: boolean;
  nextKey?: string;
  prevKey?: string;
}

function isTypingTarget(el: EventTarget | null): boolean {
  if (!(el instanceof HTMLElement)) return false;
  const tag = el.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || el.isContentEditable;
}

export function useListKeyboardNav<T extends Identifiable>({
  items,
  selectedId,
  onSelect,
  enabled = true,
  nextKey = 'j',
  prevKey = 'k',
}: Options<T>): void {
  useEffect(() => {
    if (!enabled) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key !== nextKey && e.key !== prevKey) return;
      if (isTypingTarget(e.target)) return;
      if (items.length === 0) return;
      e.preventDefault();
      const curIdx = selectedId != null
        ? items.findIndex(item => item.id === selectedId)
        : -1;
      const nextIdx = e.key === nextKey
        ? Math.min(items.length - 1, curIdx + 1)
        : Math.max(0, curIdx < 0 ? 0 : curIdx - 1);
      if (nextIdx === curIdx) return;
      const target = items[nextIdx];
      if (target) onSelect(target.id);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [items, selectedId, onSelect, enabled, nextKey, prevKey]);
}
