<script setup>
import { computed, onMounted, onUnmounted, ref } from "vue";
import { Alert, Button, FormControl, FrappeUIProvider } from "frappe-ui";
import PostForm from "./PostForm.vue";
import PasswordLogin from "./PasswordLogin.vue";

const { data } = defineProps({ data: Object });
const name = ref(data.profile?.full_name || "");
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
  document.querySelector("[data-error], [data-autofocus]")?.focus();
});
onUnmounted(() => clearInterval(timer));
const title = computed(() =>
  ["email", "password"].includes(data.page)
    ? "Sign in to Frappe ID"
    : data.page === "account"
      ? "Frappe ID account"
      : data.title,
);
const emailFields = (action) => ({
  action,
  attempt: data.attempt,
  form_token: data.form_token,
});
const accountFields = (action) => ({ action, csrf_token: data.csrf_token });
</script>

<template>
  <FrappeUIProvider>
    <main
      class="min-h-screen bg-surface-gray-1 px-4 py-10 text-ink-gray-9 sm:py-16"
    >
      <div
        class="mx-auto space-y-6"
        :class="data.page === 'account' ? 'max-w-xl' : 'max-w-sm'"
      >
        <h1 class="text-center text-2xl font-semibold">{{ title }}</h1>
        <Alert
          v-if="data.error"
          theme="red"
          :description="data.error"
          tabindex="-1"
          data-error
        />
        <div
          class="space-y-6 rounded-6 border border-outline-gray-1 bg-surface-base p-6 shadow-sm"
        >
          <PasswordLogin
            v-if="data.page === 'password'"
            :destination="data.destination"
            :enabled="data.password_enabled"
            :email-url="data.email_url"
          />
          <template v-else-if="data.page === 'email'">
            <template v-if="data.challenge && !data.challenge.spent">
              <Alert
                theme="blue"
                title="Check your inbox"
                description="If this address belongs to an eligible account, a code has been queued. Enter it in this browser within five minutes."
              />
              <p class="text-sm text-ink-gray-6">
                Request {{ data.attempt.slice(0, 8) }}, code
                {{ data.challenge.generation }}. Use the newest code for this
                request.
              </p>
              <PostForm
                action="/passport/email"
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
                <Button
                  type="submit"
                  variant="solid"
                  size="md"
                  class="w-full"
                  :loading="pending"
                  loading-text="Signing in…"
                  >Sign in</Button
                >
              </PostForm>
              <PostForm
                action="/passport/email"
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
                  >Send another code{{
                    remaining ? ` (${remaining}s)` : ""
                  }}</Button
                >
              </PostForm>
            </template>
            <PostForm
              v-else-if="!data.challenge"
              action="/passport/email"
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
              <Button
                :href="data.password_url"
                target="_self"
                variant="ghost"
                size="md"
                >Use password login</Button
              >
              <Button
                :href="`/passport/email?redirect-to=${encodeURIComponent(data.destination)}`"
                target="_self"
                variant="ghost"
                size="md"
                >Start a new email login</Button
              >
            </nav>
          </template>
          <template v-else-if="data.page === 'account'">
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
                v-model="name"
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
          </template>
          <template v-else>
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
                :href="data.link"
                target="_self"
                variant="ghost"
                size="md"
                >{{ data.link_label }}</Button
              >
            </p>
          </template>
        </div>
        <template v-if="data.page === 'account'">
          <section aria-labelledby="sessions-heading" class="space-y-4">
            <h2 id="sessions-heading" class="text-lg font-semibold">
              Browser sessions
            </h2>
            <p class="text-sm text-ink-gray-6">
              Device logout ends that issuer session and prevents renewal of its
              site sessions. Existing site access expires within five minutes.
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
      </div>
    </main>
  </FrappeUIProvider>
</template>
