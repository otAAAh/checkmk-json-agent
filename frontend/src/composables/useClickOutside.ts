// Copyright (C) 2026 Benjamin Knapp
// SPDX-License-Identifier: GPL-2.0-only
// Dismiss a lightweight popover (e.g. the JSON picker's "+ Add" menu) on an
// outside pointer press or the Escape key. Listeners are attached ONLY while
// `active` is true, so a large tree with one menu per row does not register
// hundreds of always-on document listeners.
import { onBeforeUnmount, watch, type Ref } from 'vue'

export function useClickOutside(
  active: Ref<boolean>,
  target: Ref<HTMLElement | null>,
  onDismiss: () => void,
): void {
  function onPointerDown(event: PointerEvent): void {
    const node = target.value
    if (node && !node.contains(event.target as Node | null)) {
      onDismiss()
    }
  }
  function onKeyDown(event: KeyboardEvent): void {
    if (event.key === 'Escape') {
      onDismiss()
    }
  }
  function bind(on: boolean): void {
    const method = on ? 'addEventListener' : 'removeEventListener'
    // Capture phase so the dismiss wins even if inner handlers stop propagation.
    document[method]('pointerdown', onPointerDown as EventListener, true)
    document[method]('keydown', onKeyDown as EventListener, true)
  }
  watch(active, (isActive) => bind(isActive))
  onBeforeUnmount(() => bind(false))
}
