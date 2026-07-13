<!-- Copyright (C) 2026 Benjamin Knapp -->
<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Wizard step 1: the endpoints, as a single native `List` FormEdit rendered
     from the ruleset's own FormSpec — native add/remove, inline validation, and
     the password store per entry (URL, method, TLS/redirect, auth). Extractions
     are edited in step 2. On Next we pre-flight every endpoint via the proxy and
     show a per-entry ✓/✗ result. -->
<script setup lang="ts">
import { computed } from 'vue'

import usei18n from '@/lib/i18n'

import CmkAlertBox from '@/components/CmkAlertBox.vue'
import CmkLoading from '@/components/CmkLoading.vue'
import { CmkWizardButton, CmkWizardStep } from '@/components/CmkWizard'
import CmkHeading from '@/components/typography/CmkHeading.vue'
import CmkParagraph from '@/components/typography/CmkParagraph.vue'

import { useExplorer } from '../../composables/useExplorer'
import { endpointUrl } from '../../lib/rulevalue'
import FormSpecEdit from '../components/FormSpecEdit.vue'

const { _t } = usei18n()
const {
  currentStep,
  state,
  connectionSpec,
  connectionValidation,
  preflight,
  validating,
  duplicateUrls,
  validateConnection,
} = useExplorer()

const hasResult = (i: number): boolean => Boolean(preflight[i])

// Recap: first 3 endpoint URLs, then "+ N more".
const recap = computed(() => {
  const urls = state.connections.map((c) => endpointUrl(c)).filter(Boolean)
  const shown = urls.slice(0, 3).join(', ')
  return urls.length > 3 ? `${shown}, ${_t('+ %{n} more', { n: urls.length - 3 })}` : shown
})
</script>

<template>
  <CmkWizardStep :index="2" :is-completed="() => currentStep > 2">
    <template #header>
      <CmkHeading>{{ _t('Define endpoints to query') }}</CmkHeading>
      <CmkParagraph>{{ _t('One or more HTTP/JSON endpoints to query. Each endpoint is fetched independently.') }}</CmkParagraph>
    </template>
    <template #content>
      <FormSpecEdit
        v-if="connectionSpec"
        :spec="connectionSpec.spec"
        :validation="connectionValidation"
        v-model:data="state.connections"
      />
      <p v-else class="je-step-connection__unavailable">
        {{ _t('Connection form unavailable on this site.') }}
      </p>

      <ul
        v-if="!validating && state.connections.some((_, i) => hasResult(i))"
        class="je-step-connection__results"
      >
        <li
          v-for="(connection, i) in state.connections"
          :key="i"
          class="je-step-connection__result"
        >
          <span class="je-step-connection__index">{{ i + 1 }}</span>
          <span class="je-step-connection__url">{{ endpointUrl(connection) || _t('(no URL)') }}</span>
          <span v-if="preflight[i]?.kind === 'loading'" class="je-step-connection__probing">{{ _t('Testing…') }}</span>
          <span v-else-if="preflight[i]?.kind === 'ok'" class="je-step-connection__ok">✓ {{ preflight[i]?.msg }}</span>
          <span v-else-if="preflight[i]?.kind === 'err'" class="je-step-connection__err">✗ {{ preflight[i]?.msg }}</span>
        </li>
      </ul>
      <CmkAlertBox v-if="duplicateUrls.length" variant="error" size="small">
        {{ _t('Each endpoint URL must be unique. Duplicated: %{urls}', { urls: duplicateUrls.join(', ') }) }}
      </CmkAlertBox>
      <CmkAlertBox
        v-if="!validating && state.connections.some((_, i) => preflight[i]?.kind === 'err')"
        variant="warning"
        size="small"
      >
        {{ _t('Every endpoint must respond with valid JSON before continuing — fix the ✗ endpoints above.') }}
      </CmkAlertBox>
    </template>
    <template #actions>
      <CmkWizardButton
        type="next"
        :validation-cb="validateConnection"
        :override-label="_t('Configure services to monitor')"
      />
      <CmkWizardButton type="previous" />
      <span v-if="validating" class="je-step-connection__validating">
        <CmkLoading /> {{ _t('Validating endpoints…') }}
      </span>
    </template>
    <template #recap>
      <span class="je-step-connection__recap">{{ recap }}</span>
    </template>
  </CmkWizardStep>
</template>

<style scoped>
.je-step-connection__unavailable,
.je-step-connection__probing {
  color: var(--font-color-dimmed);
}

.je-step-connection__results {
  padding: 0;
  margin: var(--dimension-4, 8px) 0 0;
  list-style: none;
}

.je-step-connection__result {
  display: flex;
  flex-wrap: wrap;
  gap: var(--dimension-4, 8px);
  align-items: baseline;
  padding: var(--dimension-3, 6px) 0;
  border-top: 1px solid var(--default-border-color);
}

.je-step-connection__index {
  min-width: 1.5em;
  color: var(--font-color-dimmed);
}

.je-step-connection__url {
  font-weight: bold;
}

.je-step-connection__ok {
  color: var(--success);
}

.je-step-connection__err {
  color: var(--color-danger, #e05a5a);
}

.je-step-connection__validating {
  display: inline-flex;
  gap: var(--dimension-3, 6px);
  align-items: center;
  margin-left: var(--dimension-4, 8px);
  color: var(--font-color-dimmed);
}

.je-step-connection__recap {
  color: var(--font-color-dimmed);
}
</style>
