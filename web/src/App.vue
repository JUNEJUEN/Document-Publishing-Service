<script setup lang="ts">
import DocumentsTable from './components/DocumentsTable.vue'
import JobList from './components/JobList.vue'
import LoginForm from './components/LoginForm.vue'
import PublishForm from './components/PublishForm.vue'
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
      <PublishForm />
      <DocumentsTable />
      <JobList />
    </template>
  </main>
</template>
