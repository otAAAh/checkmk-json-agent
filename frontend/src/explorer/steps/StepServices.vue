<!-- Copyright (C) 2026 Benjamin Knapp -->
<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Wizard step 2: the fields to monitor, per endpoint. Uses the built-in
     CmkAccordion — one item per endpoint, only one open at a time. Each item
     holds the JSON field picker (click a path to add it as a service,
     prefilling regex/levels from the sampled value) side by side with the
     ruleset's own `extractions` `List` FormEdit — unit, thresholds,
     expected-string and label-path are native FormSpec fields. -->
<script setup lang="ts">
import { computed, ref } from 'vue'

import usei18n from '@/lib/i18n'

import CmkAccordion from '@/components/CmkAccordion/CmkAccordion.vue'
import CmkAccordionItem from '@/components/CmkAccordion/CmkAccordionItem.vue'
import CmkCollapsible from '@/components/CmkCollapsible/CmkCollapsible.vue'
import CmkCollapsibleTitle from '@/components/CmkCollapsible/CmkCollapsibleTitle.vue'
import CmkIndent from '@/components/CmkIndent.vue'
import { CmkWizardButton, CmkWizardStep } from '@/components/CmkWizard'
import CmkHeading from '@/components/typography/CmkHeading.vue'
import CmkParagraph from '@/components/typography/CmkParagraph.vue'

import { useExplorer } from '../../composables/useExplorer'
import { endpointUrl, extractionPath } from '../../lib/rulevalue'
import FormSpecEdit from '../components/FormSpecEdit.vue'
import JsonPicker from '../components/JsonPicker.vue'

const {
  currentStep,
  state,
  extractionsSpec,
  hostLabelsSpec,
  sampleLoading,
  sampleErrors,
  fetchSample,
  togglePath,
  addHostLabel,
  validateServices,
  validExtractions,
} = useExplorer()

const { _t } = usei18n()

// Accordion: exactly one endpoint open at a time; the first opens by default.
const openedItems = ref<string[]>(['0'])

// The two right-column collapsibles, open per endpoint (default: both open).
const servicesOpen = ref<Record<number, boolean>>({})
const hostLabelsOpen = ref<Record<number, boolean>>({})
const isOpen = (rec: Record<number, boolean>, ei: number): boolean => rec[ei] ?? true
function toggle(rec: Record<number, boolean>, ei: number): void {
  rec[ei] = !isOpen(rec, ei)
}

const hostLabelCount = (ei: number): number => state.services[ei]?.hostLabels?.length ?? 0
const serviceCount = (ei: number): number => state.services[ei]?.extractions.length ?? 0

// Recap: "x services on y endpoints" (endpoints that have at least one service).
const recap = computed(() => {
  const services = validExtractions.value.length
  const endpoints = state.services.filter((s) =>
    s.extractions.some((x) => extractionPath(x).trim()),
  ).length
  return _t('%{services} services on %{endpoints} endpoints', { services, endpoints })
})
</script>

<template>
  <CmkWizardStep :index="3" :is-completed="() => currentStep > 3">
    <template #header>
      <CmkHeading>{{ _t('Configure services & host labels') }}</CmkHeading>
      <CmkParagraph>{{ _t('Pick the JSON fields to monitor as services, and tag the host with labels. Configure values and thresholds per service.') }}</CmkParagraph>
    </template>
    <template #content>
      <CmkAccordion v-model="openedItems" :min-open="1" :max-open="1">
        <CmkAccordionItem
          v-for="(connection, ei) in state.connections"
          :key="ei"
          :value="String(ei)"
          header-as="div"
        >
          <template #header>
            <CmkHeading type="h3" class="je-step-services__title">
              {{ endpointUrl(connection) || _t('Endpoint %{n}', { n: ei + 1 }) }}
            </CmkHeading>
            <span class="je-step-services__count">{{ _t('%{s} service(s), %{h} host label(s)', { s: serviceCount(ei), h: hostLabelCount(ei) }) }}</span>
          </template>
          <template #content>
            <div v-if="state.services[ei]" class="je-step-services__cols">
              <div class="je-step-services__col je-step-services__col--picker">
                <JsonPicker
                  v-model="state.services[ei]!.sampleJson"
                  :selected-paths="state.services[ei]!.extractions.map(extractionPath)"
                  :loading="Boolean(sampleLoading[ei])"
                  :error="sampleErrors[ei]"
                  :can-refetch="Boolean(endpointUrl(connection).trim())"
                  @refetch="() => fetchSample(ei)"
                  @toggle="(path, valueType, sampleValue) => togglePath(ei, path, valueType, sampleValue)"
                  @hostlabel="(path) => addHostLabel(ei, path)"
                />
              </div>
              <div class="je-step-services__col je-step-services__col--form">
                <CmkCollapsibleTitle
                  :title="_t('Services to monitor')"
                  :open="isOpen(servicesOpen, ei)"
                  @toggle-open="toggle(servicesOpen, ei)"
                />
                <CmkCollapsible :open="isOpen(servicesOpen, ei)">
                  <CmkIndent v-if="extractionsSpec">
                    <FormSpecEdit
                      :spec="extractionsSpec.spec"
                      v-model:data="state.services[ei]!.extractions"
                    />
                  </CmkIndent>
                </CmkCollapsible>
                <CmkCollapsibleTitle
                  :title="_t('Host labels')"
                  :open="isOpen(hostLabelsOpen, ei)"
                  @toggle-open="toggle(hostLabelsOpen, ei)"
                />
                <CmkCollapsible :open="isOpen(hostLabelsOpen, ei)">
                  <CmkIndent v-if="hostLabelsSpec">
                    <FormSpecEdit
                      :spec="hostLabelsSpec.spec"
                      v-model:data="state.services[ei]!.hostLabels"
                    />
                  </CmkIndent>
                </CmkCollapsible>
              </div>
            </div>
          </template>
        </CmkAccordionItem>
      </CmkAccordion>
    </template>
    <template #actions>
      <CmkWizardButton
        type="next"
        :validation-cb="validateServices"
        :override-label="_t('Review & create')"
      />
      <CmkWizardButton type="previous" />
    </template>
    <template #recap>
      <span class="je-step-services__recap">{{ recap }}</span>
    </template>
  </CmkWizardStep>
</template>

<style scoped>
.je-step-services__title {
  min-width: 0;
  margin: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.je-step-services__count {
  flex-shrink: 0;
  margin-left: auto;
  padding-left: var(--dimension-4, 8px);
  font-weight: normal;
  color: var(--font-color-dimmed);
}

/* Trim the accordion's default 20px 60px content padding — no deep indent —
   and let the content sit on the default page background (not the item's). */
/* stylelint-disable-next-line checkmk/vue-bem-naming-convention */
:deep(.cmk-accordion-item__content-wrapper) {
  padding: var(--dimension-4, 8px) var(--dimension-6, 16px);
  background: var(--default-bg-color);
}

.je-step-services__cols {
  display: flex;
  flex-wrap: wrap;
  gap: var(--dimension-6, 16px);
  align-items: flex-start;
}

.je-step-services__col {
  flex: 1 1 340px;
  min-width: 0;
}

.je-step-services__col--form {
  padding-left: var(--dimension-6, 16px);
  border-left: 1px solid var(--default-border-color);
}

.je-step-services__recap {
  color: var(--font-color-dimmed);
}
</style>
