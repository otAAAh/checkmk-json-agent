<!-- Copyright (C) 2026 Benjamin Knapp -->
<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Orchestrator: shared state lives in useExplorer(); each step is its own
     component under this folder. -->
<script setup lang="ts">
import { onMounted } from 'vue'

import CmkWizard from '@/components/CmkWizard'

import { useExplorer, type FormSpecPayload } from '../composables/useExplorer'
import StepConnection from './steps/StepConnection.vue'
import StepHost from './steps/StepHost.vue'
import StepReview from './steps/StepReview.vue'
import StepServices from './steps/StepServices.vue'

// The GUI page passes serialized FormSpec payload(s) via the custom element's
// `data` attribute (html.vue_component("cmk-json-explorer", {...})).
const props = defineProps<{ data?: string }>()
const { currentStep, status, setConnectionSpec, setExtractionsSpec, setPlacementSpec } =
  useExplorer()

onMounted(() => {
  if (!props.data) {
    return
  }
  try {
    const parsed = JSON.parse(props.data) as {
      connectionSpec?: FormSpecPayload
      extractionsSpec?: FormSpecPayload
      placementSpec?: FormSpecPayload
    }
    if (parsed.connectionSpec) {
      setConnectionSpec(parsed.connectionSpec)
    }
    if (parsed.extractionsSpec) {
      setExtractionsSpec(parsed.extractionsSpec)
    }
    if (parsed.placementSpec) {
      setPlacementSpec(parsed.placementSpec)
    }
  } catch {
    // ignore malformed data
  }
})
</script>

<template>
  <div class="je-app">
    <CmkWizard v-model="currentStep" mode="guided" :locked="status.kind === 'ok'">
      <StepHost />
      <StepConnection />
      <StepServices />
      <StepReview />
    </CmkWizard>
  </div>
</template>

<style scoped>
.je-app {
  max-width: 1080px;
  padding: var(--dimension-4, 8px) 0;
}

.je-app__intro {
  margin: 0 0 var(--dimension-4, 8px);
  color: var(--font-color-dimmed);
}
</style>
