<script setup>
import { Alert, Button, FormControl } from "frappe-ui";
import PostForm from "./PostForm.vue";
defineProps({ data: Object });
</script>

<template>
  <template v-if="data.stage === 'confirm'">
    <p class="text-sm text-ink-gray-7">
      You were invited to use Frappe ID with your account on
      <strong>{{ data.site }}</strong
      >.
    </p>
    <FormControl
      label="Site account"
      :model-value="data.user"
      readonly
      size="md"
    />
    <Alert
      theme="blue"
      description="Confirm your site password, then choose and approve your Frappe ID. Your site account keeps its existing permissions."
    />
    <PostForm
      action="/passport/invitation"
      :fields="{ action: 'begin', csrf_token: data.csrf_token }"
      v-slot="{ pending }"
    >
      <FormControl
        label="Confirm your site password"
        name="password"
        type="password"
        autocomplete="current-password"
        maxlength="512"
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
        loading-text="Confirming…"
        >Continue to Frappe ID</Button
      >
    </PostForm>
  </template>
  <template v-else-if="data.stage === 'accept'">
    <FormControl label="Site" :model-value="data.site" readonly size="md" />
    <FormControl
      label="Site account"
      :model-value="data.user"
      readonly
      size="md"
    />
    <FormControl
      label="Frappe ID"
      :model-value="
        data.profile.email || data.profile.name || 'Confirmed Frappe ID'
      "
      readonly
      size="md"
    />
    <Alert
      theme="blue"
      description="Use this Frappe ID to sign in to this site. Your existing application permissions stay the same."
    />
    <PostForm
      action="/passport/invitation"
      :fields="{
        action: 'accept',
        attempt: data.attempt,
        csrf_token: data.csrf_token,
      }"
      v-slot="{ pending }"
    >
      <Button
        type="submit"
        variant="solid"
        size="md"
        class="w-full"
        :loading="pending"
        loading-text="Accepting…"
        >Accept invitation</Button
      >
    </PostForm>
  </template>
  <template v-else-if="data.stage === 'success'">
    <Alert
      theme="green"
      role="status"
      description="You can now use your confirmed Frappe ID to sign in to this site."
    />
    <FormControl
      label="Site account"
      :model-value="data.user"
      readonly
      size="md"
    />
    <Button href="/" target="_self" variant="solid" size="md" class="w-full"
      >Open site</Button
    >
    <Button
      v-if="data.passport_url"
      :href="data.passport_url"
      target="_self"
      variant="ghost"
      size="md"
      >Return to Frappe ID</Button
    >
  </template>
  <template v-else>
    <p class="text-sm text-ink-gray-7">
      Sign in with the invited site's local account and reopen your invitation
      link. If the invitation has expired or was cancelled, ask the site
      administrator for a new one.
    </p>
    <Button href="/login" target="_self" variant="outline" size="md"
      >Go to site login</Button
    >
  </template>
  <Button
    v-if="data.stage !== 'success'"
    href="/"
    target="_self"
    variant="ghost"
    size="md"
    >{{ data.stage === "accept" ? "Not now" : "Return to site" }}</Button
  >
</template>
