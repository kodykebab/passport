<script setup>
import { Alert, Button, FormControl } from "frappe-ui";
import PostForm from "./PostForm.vue";
defineProps({ data: Object });
</script>
<template>
  <FormControl
    label="Site account"
    :model-value="data.user"
    readonly
    size="md"
  />
  <Alert
    v-if="data.linked"
    role="status"
    theme="green"
    description="This site account is linked to a Frappe ID. You can use Frappe ID the next time you sign in."
  />
  <template v-else>
    <Alert
      theme="blue"
      description="Confirm this site's password, then choose and approve your Frappe ID. Linking does not change your site name, roles or permissions."
    />
    <PostForm
      action="/passport/link"
      :fields="{ csrf_token: data.csrf_token }"
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
  <Button href="/" target="_self" variant="ghost" size="md"
    >Return to site</Button
  >
</template>
