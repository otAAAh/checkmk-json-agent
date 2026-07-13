<!-- Copyright (C) 2026 Benjamin Knapp -->
<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Renders a slice of the real ruleset FormSpec (the endpoint authentication)
     via Checkmk's own FormEdit — native fields, inline validation, and the
     password-store Password widget. The spec/value payload is serialized
     server-side by the GUI page (serialize_data_for_frontend) and passed into
     the app; the edited value round-trips back through the Python visitor on
     create. -->
<script setup lang="ts">
import FormEdit from '@/form/FormEdit.vue'
import type { ValidationMessages } from '@/form'
import type { FormSpec } from 'cmk-shared-typing/typescript/vue_formspec_components'

defineProps<{ spec: FormSpec; validation?: ValidationMessages }>()
const data = defineModel<unknown>('data', { required: true })
</script>

<template>
  <div class="je-form-spec-edit">
    <FormEdit v-model:data="data" :spec="spec" :backend-validation="validation ?? []" />
  </div>
</template>

<style scoped>
.je-form-spec-edit {
  display: block;
}

/* The arrow's own `padding: 0 4px` (with border-box) shrinks the width:100% SVG
   to near-zero, so the chevron renders clipped. Use a left margin for the gap
   instead, and center it at a small fixed height rather than full-height stretch. */
/* stylelint-disable-next-line checkmk/vue-bem-naming-convention */
.je-form-spec-edit :deep(.cmk-dropdown--arrow) {
  align-self: center;
  height: 0.55em;
  padding: 0;
  margin-left: 4px;
}
</style>
