<script setup>
import { ref } from "vue";
import { Alert, Button, FormControl } from "frappe-ui";
import PostForm from "./PostForm.vue";
const { data } = defineProps({ data: Object });
const displayName = ref(data.profile.full_name);
const accountFields = (action) => ({ action, csrf_token: data.csrf_token });
</script>
<template>
  <Button href="/sites" variant="outline">Your sites</Button>
  <div
    class="space-y-6 rounded-6 border border-outline-gray-1 bg-surface-base p-6 shadow-sm"
  >
    <FormControl
      label="Email"
      :model-value="data.profile.email"
      readonly
      description="Your email address cannot be changed here."
      size="md"
    />
    <PostForm
      action="/account"
      :fields="accountFields('name')"
      v-slot="{ pending }"
    >
      <FormControl
        label="Display name"
        name="display_name"
        v-model="displayName"
        maxlength="140"
        required
        size="md"
      />
      <FormControl
        label="Confirm your password"
        type="password"
        name="password"
        autocomplete="current-password"
        required
        size="md"
      />
      <Button
        type="submit"
        variant="solid"
        size="md"
        :loading="pending"
        loading-text="Saving…"
        >Save display name</Button
      >
    </PostForm>
  </div>
  <section aria-labelledby="connections-heading" class="space-y-4">
    <h2 id="connections-heading" class="text-lg font-semibold">
      Connected sites
    </h2>
    <p class="text-sm text-ink-gray-6">
      Sites authorized through this Frappe ID. Revoking access blocks sign-in
      and renewal; existing access expires within five minutes. Site accounts
      and data remain unchanged.
    </p>
    <Alert
      v-if="!data.connections?.length"
      theme="blue"
      description="No authorized sites yet."
    />
    <div
      v-for="site in data.connections"
      :key="site.client"
      class="rounded-6 border border-outline-gray-1 bg-surface-base p-6"
    >
      <PostForm
        action="/account"
        :fields="{
          ...accountFields(site.revoked ? 'allow_site' : 'revoke_site'),
          client: site.client,
        }"
        v-slot="{ pending }"
      >
        <h3 class="break-words font-semibold">{{ site.title }}</h3>
        <p class="break-all text-sm text-ink-gray-6">{{ site.origin }}</p>
        <Alert
          v-if="site.revoked"
          theme="blue"
          description="Access revoked. Allowing future sign-ins will not restore old sessions."
        />
        <FormControl
          label="Confirm your password"
          type="password"
          name="password"
          autocomplete="current-password"
          required
          size="md"
        />
        <Button
          type="submit"
          variant="outline"
          size="md"
          :loading="pending"
          loading-text="Saving…"
          >{{
            site.revoked ? "Allow future sign-ins" : "Revoke site access"
          }}</Button
        >
      </PostForm>
    </div>
  </section>
  <section aria-labelledby="sessions-heading" class="space-y-4">
    <h2 id="sessions-heading" class="text-lg font-semibold">
      Browser sessions
    </h2>
    <p class="text-sm text-ink-gray-6">
      Device logout ends that issuer session and prevents renewal of its site
      sessions. Existing site access expires within five minutes.
    </p>
    <div
      v-for="session in data.sessions"
      :key="session.device"
      class="rounded-6 border border-outline-gray-1 bg-surface-base p-6"
    >
      <PostForm
        action="/account"
        :fields="{ ...accountFields('device'), device: session.device }"
        v-slot="{ pending }"
      >
        <p class="break-words text-sm">
          {{ session.user_agent }}
          <strong v-if="session.current">(current)</strong>
        </p>
        <FormControl
          label="Confirm your password"
          type="password"
          name="password"
          autocomplete="current-password"
          required
          size="md"
        />
        <Button
          type="submit"
          variant="outline"
          size="md"
          :loading="pending"
          loading-text="Logging out…"
          >Log out this device</Button
        >
      </PostForm>
    </div>
  </section>
  <section
    class="rounded-6 border border-outline-gray-1 bg-surface-base p-6"
    aria-labelledby="logout-heading"
  >
    <PostForm
      action="/account"
      :fields="accountFields('everywhere')"
      v-slot="{ pending }"
    >
      <h2 id="logout-heading" class="text-lg font-semibold">
        Log out everywhere
      </h2>
      <FormControl
        label="Confirm your password"
        type="password"
        name="password"
        autocomplete="current-password"
        required
        size="md"
      />
      <Button
        type="submit"
        theme="red"
        variant="solid"
        size="md"
        :loading="pending"
        loading-text="Logging out…"
        >Log out everywhere</Button
      >
    </PostForm>
  </section>
</template>
