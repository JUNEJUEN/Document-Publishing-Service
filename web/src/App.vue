<script setup lang="ts">
import JobList from './components/JobList.vue'
import LoginForm from './components/LoginForm.vue'
import PublishBar from './components/PublishBar.vue'
import { logout, session } from './session'
</script>

<template>
  <main>
    <header>
      <h1>Document publishing</h1>
      <div v-if="session.token" class="who">
        <span>Logged in as {{ session.clientId }}</span>
        <button class="secondary" @click="logout()">Log out</button>
      </div>
    </header>
    <p v-if="session.notice" class="notice">{{ session.notice }}</p>
    <LoginForm v-if="!session.token" />
    <template v-else>
      <PublishBar />
      <JobList />
    </template>
  </main>
</template>
