<script setup>
import { nextTick, ref } from "vue";
import { Alert, Button, FormControl } from "frappe-ui";

const { destination, enabled, emailUrl, signupUrl } = defineProps({
  destination: String,
  enabled: Boolean,
  emailUrl: String,
  signupUrl: String,
});
const email = ref("");
const password = ref("");
const pending = ref(false);
const error = ref("");
const errorNotice = ref(null);

async function login() {
  if (pending.value) return;
  pending.value = true;
  error.value = "";
  try {
    const response = await fetch("/api/method/login", {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded",
        Accept: "application/json",
      },
      body: new URLSearchParams({
        usr: email.value.trim(),
        pwd: password.value,
      }),
    });
    const result = await response.json();
    if (response.ok && ["Logged In", "No App"].includes(result.message)) {
      window.location.assign(destination);
      return;
    }
    if (response.status === 429 || result.exc_type === "SecurityException") {
      error.value =
        "Too many password attempts. Please wait before trying again.";
    } else if (response.status === 401) {
      error.value = "Incorrect email or password. Please try again.";
    } else if (response.ok) {
      error.value =
        "Your account requires an additional sign-in step. Contact your administrator.";
    } else {
      error.value =
        "Sign-in is unavailable or not permitted. Please try again later or contact your administrator.";
    }
  } catch {
    error.value =
      "Unable to reach Frappe ID. Check your connection and try again.";
  } finally {
    password.value = "";
    pending.value = false;
    if (error.value) {
      await nextTick();
      errorNotice.value?.$el.focus();
    }
  }
}
</script>

<template>
  <Alert
    v-if="error"
    ref="errorNotice"
    theme="red"
    :description="error"
    tabindex="-1"
  />
  <form
    v-if="enabled"
    class="space-y-4"
    :aria-busy="pending"
    @submit.prevent="login"
  >
    <FormControl
      v-model="email"
      label="Email"
      type="email"
      name="usr"
      autocomplete="username"
      required
      data-autofocus
      size="md"
    />
    <FormControl
      v-model="password"
      label="Password"
      type="password"
      name="pwd"
      autocomplete="current-password"
      required
      size="md"
    />
    <Button
      type="submit"
      variant="solid"
      size="md"
      class="w-full"
      :loading="pending"
      loading-text="Signing in…"
      >Sign in</Button
    >
  </form>
  <Alert
    v-else
    theme="amber"
    description="Password login is disabled for this site."
  />
  <Button
    v-if="emailUrl"
    :href="emailUrl"
    target="_self"
    variant="outline"
    size="md"
    class="w-full"
    >Use email code</Button
  >
  <Button
    v-if="signupUrl"
    :href="signupUrl"
    target="_self"
    variant="ghost"
    size="md"
    class="w-full"
    >Create a Frappe ID</Button
  >
</template>
