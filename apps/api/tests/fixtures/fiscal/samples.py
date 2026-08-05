"""XMLs de exemplo (minimos) para testar o parser fiscal.

Sao reproduzidos a partir dos leiautes oficiais (Portal NFe v4.0,
Portal CT-e v3.0, ABRASF NFS-e 1.0, SAT CF-e 0.07/0.08, Manual de
Baixas Domínio). Mantemos o minimo necessario para extrair chave +
valor + partes -- testes de regressao do parser, nao validacao SEFAZ.
"""
from __future__ import annotations

NFE_44_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe" versao="4.00">
  <NFe>
    <infNFe Id="NFe35240414200166000187550010000123451000000001" versao="4.00">
      <ide>
        <cUF>35</cUF>
        <natOp>VENDA</natOp>
        <mod>55</mod>
        <serie>1</serie>
        <nNF>12345</nNF>
        <dhEmi>2024-04-15T10:30:00-03:00</dhEmi>
        <tpNF>1</tpNF>
      </ide>
      <emit>
        <CNPJ>14200166000187</CNPJ>
        <xNome>Construtora Primor LTDA</xNome>
      </emit>
      <dest>
        <CNPJ>33000167000101</CNPJ>
        <xNome>Petrobras Distribuidora</xNome>
      </dest>
      <total>
        <ICMSTot>
          <vNF>15750.50</vNF>
        </ICMSTot>
      </total>
    </infNFe>
  </NFe>
</nfeProc>
"""

# NF-e com itens + totais de impostos + endereco do emitente. Chave com
# DV valido (8) -- calculado pelo modulo-11 oficial -- para exercitar
# `chave_dv_valida=True` (a NFE_44_XML acima tem DV proposital/invalido).
NFE_DETALHADA_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe" versao="4.00">
  <NFe>
    <infNFe Id="NFe35240414200166000187550010000543211000000008" versao="4.00">
      <ide>
        <cUF>35</cUF>
        <natOp>VENDA</natOp>
        <mod>55</mod>
        <serie>1</serie>
        <nNF>54321</nNF>
        <dhEmi>2024-04-22T09:15:00-03:00</dhEmi>
        <tpNF>1</tpNF>
      </ide>
      <emit>
        <CNPJ>14200166000187</CNPJ>
        <xNome>Construtora Primor LTDA</xNome>
        <enderEmit>
          <xMun>Sao Paulo</xMun>
          <UF>SP</UF>
        </enderEmit>
      </emit>
      <dest>
        <CNPJ>33000167000101</CNPJ>
        <xNome>Petrobras Distribuidora</xNome>
      </dest>
      <det nItem="1">
        <prod>
          <cProd>CIM-CP2</cProd>
          <xProd>Cimento CP-II 50kg</xProd>
          <NCM>25232910</NCM>
          <CFOP>5102</CFOP>
          <uCom>SC</uCom>
          <qCom>100.0000</qCom>
          <vUnCom>200.0000000000</vUnCom>
          <vProd>20000.00</vProd>
        </prod>
      </det>
      <det nItem="2">
        <prod>
          <cProd>ACO-CA50</cProd>
          <xProd>Vergalhao CA-50 12mm</xProd>
          <NCM>72142000</NCM>
          <CFOP>5102</CFOP>
          <uCom>BR</uCom>
          <qCom>10.0000</qCom>
          <vUnCom>500.0000000000</vUnCom>
          <vProd>5000.00</vProd>
        </prod>
      </det>
      <total>
        <ICMSTot>
          <vICMS>3000.00</vICMS>
          <vIPI>250.00</vIPI>
          <vPIS>165.00</vPIS>
          <vCOFINS>760.00</vCOFINS>
          <vNF>25000.00</vNF>
        </ICMSTot>
      </total>
    </infNFe>
  </NFe>
</nfeProc>
"""

NFCE_65_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<NFe xmlns="http://www.portalfiscal.inf.br/nfe">
  <infNFe Id="NFe35240414200166000187650010000099991000000001" versao="4.00">
    <ide>
      <mod>65</mod>
      <serie>1</serie>
      <nNF>9999</nNF>
      <dhEmi>2024-04-20T14:00:00-03:00</dhEmi>
    </ide>
    <emit>
      <CNPJ>14200166000187</CNPJ>
      <xNome>Loja Primor</xNome>
    </emit>
    <dest>
      <CPF>12345678901</CPF>
      <xNome>Joao Consumidor</xNome>
    </dest>
    <total>
      <ICMSTot>
        <vNF>89.90</vNF>
      </ICMSTot>
    </total>
  </infNFe>
</NFe>
"""

CTE_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<cteProc xmlns="http://www.portalfiscal.inf.br/cte" versao="3.00">
  <CTe>
    <infCte Id="CTe35240414200166000187570010000055551000000001" versao="3.00">
      <ide>
        <cUF>35</cUF>
        <mod>57</mod>
        <serie>1</serie>
        <nCT>5555</nCT>
        <dhEmi>2024-04-18T08:00:00-03:00</dhEmi>
      </ide>
      <emit>
        <CNPJ>11222333000144</CNPJ>
        <xNome>Transportadora Beta</xNome>
      </emit>
      <dest>
        <CNPJ>14200166000187</CNPJ>
        <xNome>Construtora Primor LTDA</xNome>
      </dest>
      <vPrest>
        <vTPrest>4500.00</vTPrest>
      </vPrest>
    </infCte>
  </CTe>
</cteProc>
"""

CFE_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<CFe>
  <infCFe Id="CFe35240414200166000187599000000000123450000000001" versaoDadosEnt="0.07">
    <ide>
      <CNPJ>14200166000187</CNPJ>
      <signAC>SGR-SAT</signAC>
      <numeroCaixa>1</numeroCaixa>
      <serieSAT>900012345</serieSAT>
      <nCFe>123450</nCFe>
      <dEmi>20240415</dEmi>
    </ide>
    <emit>
      <CNPJ>14200166000187</CNPJ>
      <xNome>Loja Primor SAT</xNome>
    </emit>
    <dest>
      <CPF>12345678901</CPF>
      <xNome>Maria Cliente</xNome>
    </dest>
    <total>
      <vCFe>250.00</vCFe>
    </total>
  </infCFe>
</CFe>
"""

NFSE_ABRASF_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<CompNfse xmlns="http://www.abrasf.org.br/nfse.xsd">
  <Nfse>
    <InfNfse>
      <Numero>2024000123</Numero>
      <CodigoVerificacao>ABC123XYZ</CodigoVerificacao>
      <DataEmissao>2024-04-22T16:30:00</DataEmissao>
      <PrestadorServico>
        <Cnpj>14200166000187</Cnpj>
        <RazaoSocial>Servicos Primor LTDA</RazaoSocial>
      </PrestadorServico>
      <TomadorServico>
        <Cnpj>33000167000101</Cnpj>
        <RazaoSocial>Cliente XYZ S.A.</RazaoSocial>
      </TomadorServico>
      <ValorServicos>3200.75</ValorServicos>
    </InfNfse>
  </Nfse>
</CompNfse>
"""

BAIXA_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<Baixas>
  <BaixaParcela>
    <Numero>P-001</Numero>
    <Cnpj>14200166000187</Cnpj>
    <RazaoSocial>Construtora Primor LTDA</RazaoSocial>
    <ValorBaixa>1500.00</ValorBaixa>
    <DataBaixa>2024-04-20</DataBaixa>
  </BaixaParcela>
</Baixas>
"""

ALL_SAMPLES = {
    "nfe": NFE_44_XML,
    "nfce": NFCE_65_XML,
    "cte": CTE_XML,
    "cfe": CFE_XML,
    "nfse": NFSE_ABRASF_XML,
    "baixa": BAIXA_XML,
}
