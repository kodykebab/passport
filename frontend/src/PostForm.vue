<script setup>
import { onMounted, onUnmounted, ref } from "vue";
const props = defineProps({
  action: { type: String, required: true },
  fields: Object,
});
const pending = ref(false);
function submit(event) {
  if (pending.value) event.preventDefault();
  else pending.value = true;
}
function reset() {
  pending.value = false;
}
onMounted(() => window.addEventListener("pageshow", reset));
onUnmounted(() => window.removeEventListener("pageshow", reset));
</script>

<template>
  <form
    method="post"
    :action="props.action"
    class="space-y-4"
    :aria-busy="pending"
    @submit="submit"
  >
    <input
      v-for="(value, name) in fields"
      :key="name"
      type="hidden"
      :name="name"
      :value="value"
    />
    <slot :pending="pending" />
  </form>
</template>
