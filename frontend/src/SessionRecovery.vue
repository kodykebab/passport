<script setup>
import { Alert, Button } from "frappe-ui";
import { ref } from "vue";
import PostForm from "./PostForm.vue";
const props = defineProps({ data: Object });
const loggingOut = ref(false);
const logoutError = ref("");
async function logout() {
  if (loggingOut.value) return;
  loggingOut.value = true;
  logoutError.value = "";
  try {
    const response = await fetch("/api/method/logout", {
      method: "POST",
      headers: { "X-Frappe-CSRF-Token": props.data.csrf_token },
    });
    if (!response.ok) throw new Error("Logout failed");
    window.location.assign("/login");
  } catch {
    logoutError.value = "Could not log out. Please try again.";
    loggingOut.value = false;
  }
}
</script>
<template>
  <Alert theme="blue" :description="data.message" />
  <PostForm
    v-if="data.csrf_token"
    action="/passport/retry"
    :fields="{ csrf_token: data.csrf_token, response_format: 'html' }"
    v-slot="{ pending }"
  >
    <Button
      type="submit"
      variant="solid"
      size="md"
      class="w-full"
      :loading="pending"
      loading-text="Validating…"
      >Retry validation</Button
    >
  </PostForm>
  <p class="text-center text-sm">
    <Button
      v-if="data.logout"
      variant="ghost"
      size="md"
      :loading="loggingOut"
      @click="logout"
      >{{ data.link_label }}</Button
    >
    <Button v-else :href="data.link" target="_self" variant="ghost" size="md">{{
      data.link_label
    }}</Button>
  </p>
  <Alert v-if="logoutError" theme="red" :description="logoutError" />
</template>
