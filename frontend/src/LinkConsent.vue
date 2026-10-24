<script setup>
import { Alert, Button, FormControl } from "frappe-ui";
import PostForm from "./PostForm.vue";
defineProps({ data: Object });
</script>
<template>
  <p class="text-sm text-ink-gray-7">
    Link your Frappe ID to your existing account on
    <strong>{{ data.client_title }}</strong>.
  </p>
  <FormControl label="Site" :model-value="data.origin" readonly size="md" />
  <FormControl
    label="Frappe ID email"
    :model-value="data.profile.email"
    readonly
    size="md"
  />
  <FormControl
    label="Display name"
    :model-value="data.profile.full_name"
    readonly
    size="md"
  />
  <Alert
    theme="blue"
    description="This site will receive your identity identifier, email and display name. Your site account keeps its existing name and permissions. Approve only if you just confirmed your site password."
  />
  <PostForm
    action="/passport/consent"
    :fields="{
      request_id: data.request_id,
      csrf_token: data.csrf_token,
      decision: 'allow',
    }"
    v-slot="{ pending }"
  >
    <Button
      type="submit"
      variant="solid"
      size="md"
      class="w-full"
      :loading="pending"
      loading-text="Linking…"
      >Link this Frappe ID</Button
    >
  </PostForm>
  <PostForm
    action="/passport/consent"
    :fields="{
      request_id: data.request_id,
      csrf_token: data.csrf_token,
      decision: 'cancel',
    }"
    v-slot="{ pending }"
  >
    <Button
      type="submit"
      variant="outline"
      size="md"
      class="w-full"
      :loading="pending"
      loading-text="Cancelling…"
      >Cancel linking</Button
    >
  </PostForm>
</template>
