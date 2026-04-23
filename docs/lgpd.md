# Conformidade LGPD

O sistema transaciona dados médicos (ASOs), documentos pessoais de funcionários
(CPF, RG, carteira de trabalho), processos INSS e histórico criminal.
Requisitos mínimos:

## Encryption

- **At rest:** Postgres com disco criptografado (LUKS / EBS encryption) +
  colunas sensíveis (CPF, dados médicos) com `pgcrypto`.
- **In transit:** TLS obrigatório em todos os endpoints públicos. Mutual TLS
  para integrações internas B2B onde aplicável.
- **Object storage:** server-side encryption no MinIO/S3.

## Audit log (`app/audit/models.py`)

Toda operação de leitura ou modificação de recurso sensível deve gerar uma
entrada em `audit_log`:
- `actor` (user_id + role)
- `action` (read, create, update, delete, export)
- `resource` (ex: `dp_sesmt.employee`)
- `resource_id`
- `metadata_json` (contexto opcional: IP, razão do acesso, justificativa)
- `created_at`

Retenção mínima: **5 anos** após o término do vínculo.

## Acesso

- RBAC estrito por módulo (RH não acessa dados financeiros e vice-versa).
- Justificativa obrigatória para acesso a ASOs e processos INSS (loga em
  `metadata_json`).
- Revogação automática quando funcionário sai do sistema de ponto (Tangerino).

## Tratamento de dados sensíveis especiais

- **Reconhecimento facial (Módulo E):** consentimento explícito dos
  funcionários; retenção da base biométrica limitada ao vínculo +
  obrigações legais.
- **Histórico criminal (Módulo A, dossiê):** acesso restrito a usuários com
  role `dp_admissao`; log obrigatório com justificativa.

## DPO e incidentes

Processo de reporte de incidentes a ser definido com o jurídico da ZAG.
Ponto único: `dpo@<dominio-zag>`.
