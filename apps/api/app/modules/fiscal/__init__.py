"""Modulo C - Fiscal: documentos fiscais (NF-e/NFS-e/NFC-e/CT-e/CF-e/Baixa).

Implementa upload + persistencia + envio ao escritorio contabil via
API Dominio Sistemas (Central do Desenvolvedor).

Decisoes de design:
  - O XML e a fonte da verdade. Salvamos o arquivo bruto no storage
    abstraido (`EditaisStorage` -- local FS ou OneDrive) e extraimos
    apenas metadata para o banco (chave_acesso, valor, datas, partes).
  - Idempotencia por (tipo, chave_acesso). CF-e e Baixa nao tem chave
    universal, entao caimos no SHA-256 do XML.
  - Envio ao Dominio e assincrono: endpoint `/enviar-dominio` enfileira
    a task no worker; o worker faz retry com backoff e atualiza
    `status_envio` + `protocolo_dominio`.
  - Sem credenciais Dominio, o adapter cai no `DominioMockClient`
    deterministico para nao bloquear dev/CI.
"""
