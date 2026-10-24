<script setup>
import { onMounted, onUnmounted, ref } from "vue";
import { Alert, Button, FormControl } from "frappe-ui";
import PostForm from "./PostForm.vue";
const { data } = defineProps({ data: Object });
const signingUp = data.purpose === "signup";
const path = data.path || "/passport/email";
const remaining = ref(data.wait || 0);
const resendAt = Date.now() + remaining.value * 1000;
let timer;
onMounted(() => {
  if (remaining.value > 0) {
    timer = setInterval(() => {
      remaining.value = Math.max(0, Math.ceil((resendAt - Date.now()) / 1000));
      if (!remaining.value) clearInterval(timer);
    }, 1000);
  }
});
onUnmounted(() => clearInterval(timer));
const emailFields = (action) => ({
  action,
  attempt: data.attempt,
  form_token: data.form_token,
});
</script>
<template>
  <template v-if="data.challenge && !data.challenge.spent">
    <Alert
      theme="blue"
      title="Check your inbox"
      :description="
        signingUp
          ? 'If this address can be registered, a code has been queued. Already registered? Use sign in.'
          : 'If this address belongs to an eligible account, a code has been queued. Enter it in this browser within five minutes.'
      "
    />
    <p class="text-sm text-ink-gray-6">
      Request {{ data.attempt.slice(0, 8) }}, code
      {{ data.challenge.generation }}. Use the newest code for this request.
    </p>
    <PostForm
      :action="path"
      :fields="{
        ...emailFields('verify'),
        generation: data.challenge.generation,
      }"
      v-slot="{ pending }"
    >
      <FormControl
        label="Code"
        name="code"
        inputmode="numeric"
        autocomplete="one-time-code"
        pattern="[0-9]{6}"
        maxlength="6"
        required
        data-autofocus
        size="md"
      />
      <template v-if="signingUp">
        <FormControl
          label="Display name"
          name="display_name"
          autocomplete="name"
          maxlength="140"
          required
          size="md"
        />
        <FormControl
          label="Choose a password"
          type="password"
          name="password"
          autocomplete="new-password"
          maxlength="512"
          required
          size="md"
          description="You will need this password to change account details or log out devices."
        />
        <Alert
          theme="blue"
          description="This creates your Frappe ID only. Access to other sites requires their administrator's approval."
        />
      </template>
      <Button
        type="submit"
        variant="solid"
        size="md"
        class="w-full"
        :loading="pending"
        :loading-text="signingUp ? 'Creating account…' : 'Signing in…'"
        >{{ signingUp ? "Create Frappe ID" : "Sign in" }}</Button
      >
    </PostForm>
    <PostForm
      :action="path"
      :fields="emailFields('resend')"
      v-slot="{ pending }"
    >
      <Button
        type="submit"
        variant="outline"
        size="md"
        class="w-full"
        :loading="pending"
        :disabled="remaining > 0"
        loading-text="Sending…"
        >Send another code{{ remaining ? ` (${remaining}s)` : "" }}</Button
      >
    </PostForm>
  </template>
  <PostForm
    v-else-if="!data.challenge"
    :action="path"
    :fields="{
      ...emailFields('send'),
      'redirect-to': data.destination,
    }"
    v-slot="{ pending }"
  >
    <FormControl
      label="Email"
      name="email"
      type="email"
      autocomplete="email"
      maxlength="140"
      required
      data-autofocus
      size="md"
    />
    <Button
      type="submit"
      variant="solid"
      size="md"
      class="w-full"
      :loading="pending"
      loading-text="Sending…"
      >Send code</Button
    >
  </PostForm>
  <Alert
    v-else
    theme="amber"
    description="This attempt has ended. Start a new login."
  />
  <nav
    aria-label="Other sign-in options"
    class="flex flex-col gap-4 text-center text-sm"
  >
    <Button :href="data.password_url" target="_self" variant="ghost" size="md"
      >Use password login</Button
    >
    <Button
      :href="`${path}?redirect-to=${encodeURIComponent(data.destination)}`"
      target="_self"
      variant="ghost"
      size="md"
      >{{
        signingUp ? "Start a new signup" : "Start a new email login"
      }}</Button
    >
    <Button
      v-if="!signingUp && data.signup_url"
      :href="data.signup_url"
      target="_self"
      variant="ghost"
      size="md"
      >Create a Frappe ID</Button
    >
  </nav>
</template>
