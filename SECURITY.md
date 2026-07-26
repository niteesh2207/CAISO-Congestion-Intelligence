# Security

## Public repository rule

Do not commit secrets or credentials to this repository.

Never place the following in `index.html`, client-side JavaScript, or other browser-delivered files:

- OpenAI or other LLM API keys
- Private CAISO / market-data credentials
- Database credentials
- Cloud access keys
- Proprietary scoring logic intended to remain confidential

## Future production architecture

The public browser application should call a server-side API. The backend should own authentication, premium data access, constraint resolution, PTDF/LODF calculations, proprietary ranking logic, and LLM calls.

If a credential is accidentally committed, revoke it immediately before removing it from Git history.
