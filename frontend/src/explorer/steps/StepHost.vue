<!-- Copyright (C) 2026 Benjamin Knapp -->
<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Wizard step 1: the rule's conditions — target folder + host — as a small
     synthesized Dictionary FormEdit (native labels + dotted-line layout,
     consistent with the other steps). -->
<script setup lang="ts">
import { computed } from 'vue'

import usei18n from '@/lib/i18n'

import CmkAlertBox from '@/components/CmkAlertBox.vue'
import { CmkWizardButton, CmkWizardStep } from '@/components/CmkWizard'
import CmkHeading from '@/components/typography/CmkHeading.vue'
import CmkParagraph from '@/components/typography/CmkParagraph.vue'

import { useExplorer } from '../../composables/useExplorer'
import FormSpecEdit from '../components/FormSpecEdit.vue'

const { _t } = usei18n()
const { currentStep, placementSpec, placementData, host, hostChoice, validateConditions, conditionsInvalid } =
  useExplorer()

// The folder value is a hashed SingleChoice token client-side (only the server
// visitor yields the real path), so the recap shows the host + mode, which are
// plain strings we can read directly.
const recap = computed(() => {
  const where = hostChoice.value.mode === 'new' ? _t('new host') : _t('existing host')
  return host.value ? _t('Host: %{host} (%{where})', { host: host.value, where }) : _t('Host: (none)')
})
</script>

<template>
  <CmkWizardStep :index="1" :is-completed="() => currentStep > 1">
    <template #header>
      <CmkHeading>{{ _t('Configure host & folder') }}</CmkHeading>
      <CmkParagraph>{{ _t('Where the rule is created — the target folder and the host it applies to.') }}</CmkParagraph>
    </template>
    <template #content>
      <FormSpecEdit
        v-if="placementSpec"
        :spec="placementSpec.spec"
        v-model:data="placementData"
      />
      <p v-else class="je-step-host__unavailable">
        {{ _t('Conditions form unavailable on this site.') }}
      </p>
      <CmkAlertBox v-if="conditionsInvalid" variant="error" size="small">
        {{ _t('Choose or enter a host before continuing.') }}
      </CmkAlertBox>
    </template>
    <template #actions>
      <CmkWizardButton
        type="next"
        :validation-cb="validateConditions"
        :override-label="_t('Define endpoints to query')"
      />
    </template>
    <template #recap>
      <span class="je-step-host__recap">{{ recap }}</span>
    </template>
  </CmkWizardStep>
</template>

<style scoped>
.je-step-host__unavailable {
  color: var(--font-color-dimmed);
}

.je-step-host__recap {
  color: var(--font-color-dimmed);
}
</style>
