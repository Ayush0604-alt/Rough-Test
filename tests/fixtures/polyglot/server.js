const express = require('express');
const { loadUser, saveUser } = require('./store');

const MAX_RETRIES = 3;
const app = express();

class UserService {
  constructor(db) { this.db = db; }

  async fetch(id) {
    if (!id) { throw new Error('id required'); }
    return await loadUser(this.db, id);
  }

  async persist(user) {
    return await saveUser(this.db, user);
  }
}

function buildService(db) {
  return new UserService(db);
}

app.get('/users/:id', async (req, res) => {
  const svc = buildService(app.locals.db);
  const user = await svc.fetch(req.params.id);
  res.json(user);
});

app.post('/users', async (req, res) => {
  const svc = buildService(app.locals.db);
  await svc.persist(req.body);
  res.status(201).send();
});

module.exports = { app, UserService, buildService, MAX_RETRIES };
