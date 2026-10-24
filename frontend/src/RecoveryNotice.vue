<script setup>
import { ref } from "vue";
import { Alert, Button } from "frappe-ui";
const { retry } = defineProps({ retry: Function });
const pending = ref(false);
async function validate() {
  pending.value = true;
  try {
    await retry();
  } finally {
    pending.value = false;
  }
}
</script>
<template>
  <div
    class="rounded-6 border border-outline-gray-2 bg-surface-base p-4 font-sans text-ink-gray-9 shadow-lg"
  >
    <Alert
      theme="amber"
      title="Frappe ID validation is needed"
      description="Access stops at lease expiry. Interrupted actions will not be replayed."
    />
    <Button
      class="mt-4"
      variant="solid"
      size="md"
      :loading="pending"
      loading-text="Validating…"
      @click="validate"
      >Retry validation</Button
    >
  </div>
</template>
