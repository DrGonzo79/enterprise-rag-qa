# The discordant cases — verification worksheet

**23 discordant pairs decide the confirmatory result** (b = 3, c = 20, p = 0.000488, two-sided exact, alpha = 0.05). McNemar conditions on the discordant set, so the other 97 questions contribute nothing to `p`. These are the cases that carry the finding.

**Every gold label below is `human_verified: false`.** It was written from corpus text and checked against corpus text by the authoring agent, not by the repository owner. The task here is to confirm or reject each one.

**How far `p` moves if golds are wrong.** Treating each correction as removing one `c` pair: 20–3 gives p = 0.000488, 17–3 gives 0.0026, 14–3 gives 0.0127, **12–3 gives 0.0352 (still significant)**, and **11–3 gives 0.0574 — not significant**. So the result survives up to **eight** corrections and fails at the ninth. A correction that *flips* a case to `b` rather than removing it costs more than one: 4–19 is two steps, not one.

---

## Part 1 — the 20 cases vector-only found and hybrid did not (`c`)

In every one of these the gold chunk is **absent from hybrid's top 8 entirely**, not merely ranked lower.

### 1. `con-006` — natural-language, eu-ai-act

**Question.** What is the largest fine for breaking the rules on banned uses of AI?

**Gold label.** `EU AI Act › CHAPTER XII › Article 99`

**Why that label was chosen.** Art 99(3): non-compliance with the Art 5 prohibitions is subject to fines of up to EUR 35 000 000 or 7 % of total worldwide annual turnover, whichever is higher.

**Vector-only found it at rank 2. Hybrid did not return it.**

**Corpus text under that label** — 2 chunk(s):

> **`EU AI Act › CHAPTER XII › Article 99 — Penalties`**
>
> EU AI Act › CHAPTER XII › Article 99 — Penalties
> 1. In accordance with the terms and conditions laid down in this Regulation, Member States shall lay down the rules on penalties and other enforcement measures, which may also include warnings and non-monetary measures, applicable to infringements of this Regulation by operators, and shall take all measures necessary to ensure that they are properly and effectively implemented, thereby taking into account the guidelines issued by the Commission pursuant to Article 96. The penalties provided for shall be effective, proportionate and dissuasive. They shall take into account the interests of SMEs, including start-ups, and their economic viability. 2. The Member States shall, without delay and at the latest by the date of entry into application, notify the Commission of the rules on penalties and of other enforcement measures referred to in paragraph 1, and shall notify it, without delay, of any subsequent amendment to them. 3. Non-compliance with the prohibition of the AI practices referred to in Article 5 shall be subject to administrative fines of up to EUR 35 000 000 or, if the offender is an undertaking, up to 7 % of its total worldwide annual turnover for the preceding financial year, whichever is higher. 4. Non-compliance with any of the following provisions related to operators or notified bodies, other than those laid down in Articles 5, shall be subject to administrative fines of up to EUR 15 000 000 or, if the offender is an undertaking, up to 3 % of its total worldwide annual turnover for the preceding financial year, whichever is higher: (a) obligations of providers pursuant to Article 16; (b) obligations of authorised representatives pursuant to Article 22; (c) obligations of importers pursuant to Article 23; (d) obligations of distributors pursuant to Article 24; (e) obligations of deployers pursuant to Article 26; (f) requirements and obligations of notified bodies pursuant to Article 31, Article 33(1), (3) and (4) or Article 34; (g) transparency obligations for providers and deployers pursuant to Article 50. 5. The supply of incorrect, incomplete or misleading information to notified bodies or national competent authorities in reply to a request shall be subject to administrative fines of up to EUR 7 500 000 or, if the offender is an undertaking, up to 1 % of its total worldwide annual turnover for the preceding financial year, whichever is higher. 6. In the case of SMEs, including start-ups, each fine referred to in this Article shall be up to the percentages or amount referred to in paragraphs 3, 4 and 5, whichever thereof is lower. 7. When deciding whether to impose an administrative fine and when deciding on the amount of the administrative fine in each individual case, all relevant circumstances of the specific situation shall be taken into account and, as appropriate, regard shall be given to the following: (a) the nature, gravity and duration of the infringement and of its consequences, taking into account the purpose of the AI system, as well as, where appropriate, the number of affected persons and the level of damage suffered by them; (b) whether administrative fines have already been applied by other market surveillance authorities to the same operator for the same infringement; (c) whether administrative fines have already been applied by other authorities to the same operator for infringements of other Union or national law, when such infringements result from the same activity or omission constituting a relevant infringement of this Regulation; (d) the size, the annual turnover and market share of the operator committing the infringement; (e) any other aggravating or mitigating factor applicable to the circumstances of the case, such as financial benefits gained, or losses avoided, directly or indirectly, from the infringement;

> **`EU AI Act › CHAPTER XII › Article 99 — Penalties`**
>
> EU AI Act › CHAPTER XII › Article 99 — Penalties
> (b) whether administrative fines have already been applied by other market surveillance authorities to the same operator for the same infringement; (c) whether administrative fines have already been applied by other authorities to the same operator for infringements of other Union or national law, when such infringements result from the same activity or omission constituting a relevant infringement of this Regulation; (d) the size, the annual turnover and market share of the operator committing the infringement; (e) any other aggravating or mitigating factor applicable to the circumstances of the case, such as financial benefits gained, or losses avoided, directly or indirectly, from the infringement; (f) the degree of cooperation with the national competent authorities, in order to remedy the infringement and mitigate the possible adverse effects of the infringement; (g) the degree of responsibility of the operator taking into account the technical and organisational measures implemented by it; (h) the manner in which the infringement became known to the national competent authorities, in particular whether, and if so to what extent, the operator notified the infringement; (i) the intentional or negligent character of the infringement; (j) any action taken by the operator to mitigate the harm suffered by the affected persons. 8. Each Member State shall lay down rules on to what extent administrative fines may be imposed on public authorities and bodies established in that Member State. 9. Depending on the legal system of the Member States, the rules on administrative fines may be applied in such a manner that the fines are imposed by competent national courts or by other bodies, as applicable in those Member States. The application of such rules in those Member States shall have an equivalent effect. 10. The exercise of powers under this Article shall be subject to appropriate procedural safeguards in accordance with Union and national law, including effective judicial remedies and due process. 11. Member States shall, on an annual basis, report to the Commission about the administrative fines they have issued during that year, in accordance with this Article, and about any related litigation or judicial proceedings.

---

### 2. `con-009` — natural-language, eu-ai-act

**Question.** Do these rules reach a company based outside Europe?

**Gold label.** `EU AI Act › CHAPTER I › Article 2`

**Why that label was chosen.** Art 2(1)(a) and (c): providers placing systems on the Union market irrespective of whether they are established in the Union or in a third country, and providers and deployers in a third country where the output is used in the Union.

**Vector-only found it at rank 5. Hybrid did not return it.**

**Corpus text under that label** — 2 chunk(s):

> **`EU AI Act › CHAPTER I › Article 2 — Scope`**
>
> EU AI Act › CHAPTER I › Article 2 — Scope
> 1. This Regulation applies to: (a) providers placing on the market or putting into service AI systems or placing on the market general-purpose AI models in the Union, irrespective of whether those providers are established or located within the Union or in a third country; (b) deployers of AI systems that have their place of establishment or are located within the Union; (c) providers and deployers of AI systems that have their place of establishment or are located in a third country, where the output produced by the AI system is used in the Union; (d) importers and distributors of AI systems; (e) product manufacturers placing on the market or putting into service an AI system together with their product and under their own name or trademark; (f) authorised representatives of providers, which are not established in the Union; (g) affected persons that are located in the Union. 2. For AI systems classified as high-risk AI systems in accordance with Article 6(1) related to products covered by the Union harmonisation legislation listed in Section B of Annex I, only Article 6(1), Articles 102 to 109 and Article 112 apply. Article 57 applies only in so far as the requirements for high-risk AI systems under this Regulation have been integrated in that Union harmonisation legislation. 3. This Regulation does not apply to areas outside the scope of Union law, and shall not, in any event, affect the competences of the Member States concerning national security, regardless of the type of entity entrusted by the Member States with carrying out tasks in relation to those competences. This Regulation does not apply to AI systems where and in so far they are placed on the market, put into service, or used with or without modification exclusively for military, defence or national security purposes, regardless of the type of entity carrying out those activities. This Regulation does not apply to AI systems which are not placed on the market or put into service in the Union, where the output is used in the Union exclusively for military, defence or national security purposes, regardless of the type of entity carrying out those activities. 4. This Regulation applies neither to public authorities in a third country nor to international organisations falling within the scope of this Regulation pursuant to paragraph 1, where those authorities or organisations use AI systems in the framework of international cooperation or agreements for law enforcement and judicial cooperation with the Union or with one or more Member States, provided that such a third country or international organisation provides adequate safeguards with respect to the protection of fundamental rights and freedoms of individuals. 5. This Regulation shall not affect the application of the provisions on the liability of providers of intermediary services as set out in Chapter II of Regulation (EU) 2022/2065. 6. This Regulation does not apply to AI systems or AI models, including their output, specifically developed and put into service for the sole purpose of scientific research and development. 7. Union law on the protection of personal data, privacy and the confidentiality of communications applies to personal data processed in connection with the rights and obligations laid down in this Regulation. This Regulation shall not affect Regulation (EU) 2016/679 or (EU) 2018/1725, or Directive 2002/58/EC or (EU) 2016/680, without prejudice to Article 10(5) and Article 59 of this Regulation. 8. This Regulation does not apply to any research, testing or development activity regarding AI systems or AI models prior to their being placed on the market or put into service. Such activities shall be conducted in accordance with applicable Union law. Testing in real world conditions shall not be covered by that exclusion. 9. This Regulation is without prejudice to the rules laid down by other Union legal acts related to consumer protection and product safety.

> **`EU AI Act › CHAPTER I › Article 2 — Scope`**
>
> EU AI Act › CHAPTER I › Article 2 — Scope
> This Regulation shall not affect Regulation (EU) 2016/679 or (EU) 2018/1725, or Directive 2002/58/EC or (EU) 2016/680, without prejudice to Article 10(5) and Article 59 of this Regulation. 8. This Regulation does not apply to any research, testing or development activity regarding AI systems or AI models prior to their being placed on the market or put into service. Such activities shall be conducted in accordance with applicable Union law. Testing in real world conditions shall not be covered by that exclusion. 9. This Regulation is without prejudice to the rules laid down by other Union legal acts related to consumer protection and product safety. 10. This Regulation does not apply to obligations of deployers who are natural persons using AI systems in the course of a purely personal non-professional activity. 11. This Regulation does not preclude the Union or Member States from maintaining or introducing laws, regulations or administrative provisions which are more favourable to workers in terms of protecting their rights in respect of the use of AI systems by employers, or from encouraging or allowing the application of collective agreements which are more favourable to workers. 12. This Regulation does not apply to AI systems released under free and open-source licences, unless they are placed on the market or put into service as high-risk AI systems or as an AI system that falls under Article 5 or 50.

---

### 3. `con-010` — natural-language, eu-ai-act

**Question.** Does a chatbot have to tell people they are not talking to a human?

**Gold label.** `EU AI Act › CHAPTER IV › Article 50`

**Why that label was chosen.** Art 50(1): systems intended to interact directly with natural persons shall be designed so that those persons are informed that they are interacting with an AI system, unless it is obvious.

**Vector-only found it at rank 1. Hybrid did not return it.**

**Corpus text under that label** — 2 chunk(s):

> **`EU AI Act › CHAPTER IV › Article 50 — Transparency obligations for providers and deployers of certain AI systems`**
>
> EU AI Act › CHAPTER IV › Article 50 — Transparency obligations for providers and deployers of certain AI systems
> 1. Providers shall ensure that AI systems intended to interact directly with natural persons are designed and developed in such a way that the natural persons concerned are informed that they are interacting with an AI system, unless this is obvious from the point of view of a natural person who is reasonably well-informed, observant and circumspect, taking into account the circumstances and the context of use. This obligation shall not apply to AI systems authorised by law to detect, prevent, investigate or prosecute criminal offences, subject to appropriate safeguards for the rights and freedoms of third parties, unless those systems are available for the public to report a criminal offence. 2. Providers of AI systems, including general-purpose AI systems, generating synthetic audio, image, video or text content, shall ensure that the outputs of the AI system are marked in a machine-readable format and detectable as artificially generated or manipulated. Providers shall ensure their technical solutions are effective, interoperable, robust and reliable as far as this is technically feasible, taking into account the specificities and limitations of various types of content, the costs of implementation and the generally acknowledged state of the art, as may be reflected in relevant technical standards. This obligation shall not apply to the extent the AI systems perform an assistive function for standard editing or do not substantially alter the input data provided by the deployer or the semantics thereof, or where authorised by law to detect, prevent, investigate or prosecute criminal offences. 3. Deployers of an emotion recognition system or a biometric categorisation system shall inform the natural persons exposed thereto of the operation of the system, and shall process the personal data in accordance with Regulations (EU) 2016/679 and (EU) 2018/1725 and Directive (EU) 2016/680, as applicable. This obligation shall not apply to AI systems used for biometric categorisation and emotion recognition, which are permitted by law to detect, prevent or investigate criminal offences, subject to appropriate safeguards for the rights and freedoms of third parties, and in accordance with Union law. 4. Deployers of an AI system that generates or manipulates image, audio or video content constituting a deep fake, shall disclose that the content has been artificially generated or manipulated. This obligation shall not apply where the use is authorised by law to detect, prevent, investigate or prosecute criminal offence. Where the content forms part of an evidently artistic, creative, satirical, fictional or analogous work or programme, the transparency obligations set out in this paragraph are limited to disclosure of the existence of such generated or manipulated content in an appropriate manner that does not hamper the display or enjoyment of the work. Deployers of an AI system that generates or manipulates text which is published with the purpose of informing the public on matters of public interest shall disclose that the text has been artificially generated or manipulated. This obligation shall not apply where the use is authorised by law to detect, prevent, investigate or prosecute criminal offences or where the AI-generated content has undergone a process of human review or editorial control and where a natural or legal person holds editorial responsibility for the publication of the content. 5. The information referred to in paragraphs 1 to 4 shall be provided to the natural persons concerned in a clear and distinguishable manner at the latest at the time of the first interaction or exposure. The information shall conform to the applicable accessibility requirements. 6. Paragraphs 1 to 4 shall not affect the requirements and obligations set out in Chapter III, and shall be without prejudice to other transparency obligations laid down in Union or national law for deployers of AI systems.

> **`EU AI Act › CHAPTER IV › Article 50 — Transparency obligations for providers and deployers of certain AI systems`**
>
> EU AI Act › CHAPTER IV › Article 50 — Transparency obligations for providers and deployers of certain AI systems
> This obligation shall not apply where the use is authorised by law to detect, prevent, investigate or prosecute criminal offences or where the AI-generated content has undergone a process of human review or editorial control and where a natural or legal person holds editorial responsibility for the publication of the content. 5. The information referred to in paragraphs 1 to 4 shall be provided to the natural persons concerned in a clear and distinguishable manner at the latest at the time of the first interaction or exposure. The information shall conform to the applicable accessibility requirements. 6. Paragraphs 1 to 4 shall not affect the requirements and obligations set out in Chapter III, and shall be without prejudice to other transparency obligations laid down in Union or national law for deployers of AI systems. 7. The AI Office shall encourage and facilitate the drawing up of codes of practice at Union level to facilitate the effective implementation of the obligations regarding the detection and labelling of artificially generated or manipulated content. The Commission may adopt implementing acts to approve those codes of practice in accordance with the procedure laid down in Article 56 (6). If it deems the code is not adequate, the Commission may adopt an implementing act specifying common rules for the implementation of those obligations in accordance with the examination procedure laid down in Article 98(2).

---

### 4. `con-011` — natural-language, eu-ai-act

**Question.** Who checks a high-risk system before it can be sold?

**Gold label.** `EU AI Act › CHAPTER III › SECTION 5 › Article 43`

**Why that label was chosen.** Art 43(1): the provider opts for either internal control under Annex VI or an assessment involving a notified body under Annex VII, depending on the system.

**Vector-only found it at rank 6. Hybrid did not return it.**

**Corpus text under that label** — 2 chunk(s):

> **`EU AI Act › CHAPTER III › SECTION 5 › Article 43 — Conformity assessment`**
>
> EU AI Act › CHAPTER III › SECTION 5 › Article 43 — Conformity assessment
> 1. For high-risk AI systems listed in point 1 of Annex III, where, in demonstrating the compliance of a high-risk AI system with the requirements set out in Section 2, the provider has applied harmonised standards referred to in Article 40, or, where applicable, common specifications referred to in Article 41, the provider shall opt for one of the following conformity assessment procedures based on: (a) the internal control referred to in Annex VI; or (b) the assessment of the quality management system and the assessment of the technical documentation, with the involvement of a notified body, referred to in Annex VII. In demonstrating the compliance of a high-risk AI system with the requirements set out in Section 2, the provider shall follow the conformity assessment procedure set out in Annex VII where: (a) harmonised standards referred to in Article 40 do not exist, and common specifications referred to in Article 41 are not available; (b) the provider has not applied, or has applied only part of, the harmonised standard; (c) the common specifications referred to in point (a) exist, but the provider has not applied them; (d) one or more of the harmonised standards referred to in point (a) has been published with a restriction, and only on the part of the standard that was restricted. For the purposes of the conformity assessment procedure referred to in Annex VII, the provider may choose any of the notified bodies. However, where the high-risk AI system is intended to be put into service by law enforcement, immigration or asylum authorities or by Union institutions, bodies, offices or agencies, the market surveillance authority referred to in Article 74(8) or (9), as applicable, shall act as a notified body. 2. For high-risk AI systems referred to in points 2 to 8 of Annex III, providers shall follow the conformity assessment procedure based on internal control as referred to in Annex VI, which does not provide for the involvement of a notified body. 3. For high-risk AI systems covered by the Union harmonisation legislation listed in Section A of Annex I, the provider shall follow the relevant conformity assessment procedure as required under those legal acts. The requirements set out in Section 2 of this Chapter shall apply to those high-risk AI systems and shall be part of that assessment. Points 4.3., 4.4., 4.5. and the fifth paragraph of point 4.6 of Annex VII shall also apply. For the purposes of that assessment, notified bodies which have been notified under those legal acts shall be entitled to control the conformity of the high-risk AI systems with the requirements set out in Section 2, provided that the compliance of those notified bodies with requirements laid down in Article 31(4), (5), (10) and (11) has been assessed in the context of the notification procedure under those legal acts. Where a legal act listed in Section A of Annex I enables the product manufacturer to opt out from a third-party conformity assessment, provided that that manufacturer has applied all harmonised standards covering all the relevant requirements, that manufacturer may use that option only if it has also applied harmonised standards or, where applicable, common specifications referred to in Article 41, covering all requirements set out in Section 2 of this Chapter. 4. High-risk AI systems that have already been subject to a conformity assessment procedure shall undergo a new conformity assessment procedure in the event of a substantial modification, regardless of whether the modified system is intended to be further distributed or continues to be used by the current deployer.

> **`EU AI Act › CHAPTER III › SECTION 5 › Article 43 — Conformity assessment`**
>
> EU AI Act › CHAPTER III › SECTION 5 › Article 43 — Conformity assessment
> Where a legal act listed in Section A of Annex I enables the product manufacturer to opt out from a third-party conformity assessment, provided that that manufacturer has applied all harmonised standards covering all the relevant requirements, that manufacturer may use that option only if it has also applied harmonised standards or, where applicable, common specifications referred to in Article 41, covering all requirements set out in Section 2 of this Chapter. 4. High-risk AI systems that have already been subject to a conformity assessment procedure shall undergo a new conformity assessment procedure in the event of a substantial modification, regardless of whether the modified system is intended to be further distributed or continues to be used by the current deployer. For high-risk AI systems that continue to learn after being placed on the market or put into service, changes to the high-risk AI system and its performance that have been pre-determined by the provider at the moment of the initial conformity assessment and are part of the information contained in the technical documentation referred to in point 2(f) of Annex IV, shall not constitute a substantial modification. 5. The Commission is empowered to adopt delegated acts in accordance with Article 97 in order to amend Annexes VI and VII by updating them in light of technical progress. 6. The Commission is empowered to adopt delegated acts in accordance with Article 97 in order to amend paragraphs 1 and 2 of this Article in order to subject high-risk AI systems referred to in points 2 to 8 of Annex III to the conformity assessment procedure referred to in Annex VII or parts thereof. The Commission shall adopt such delegated acts taking into account the effectiveness of the conformity assessment procedure based on internal control referred to in Annex VI in preventing or minimising the risks to health and safety and protection of fundamental rights posed by such systems, as well as the availability of adequate capacities and resources among notified bodies.

---

### 5. `con-019` — natural-language, nvidia-10k

**Question.** How many people does the company employ?

**Gold label.** `NVIDIA 10-K FY2026 › Item 1. Business › Human Capital`

**Why that label was chosen.** 'approximately 42,000 employees in 38 countries; 31,000 were engaged in research and development and 11,000 in sales, marketing, operations, and administrative positions'.

**Vector-only found it at rank 1. Hybrid did not return it.**

**Corpus text under that label** — 1 chunk(s):

> **`NVIDIA 10-K FY2026 › Item 1. Business › Human Capital Management`**
>
> NVIDIA 10-K FY2026 › Item 1. Business › Human Capital Management
> As of the end of fiscal year 2026, we had approximately 42,000 employees in 38 countries; 31,000 were engaged in research and development and 11,000 were engaged in sales, marketing, operations, and administrative positions. To execute our business strategy successfully, we focus on recruiting, developing, and retaining top global talent. Within our workforce, more than 80 percent have technical roles and more than half of the workforce hold an advanced degree. Our employees also help to surface top talent, with over 40 percent of our new hires in fiscal year 2026 coming from employee referrals. In fiscal year 2026, our turnover rate was 3.7 percent. We invest in employee development through on-the-job trainings and tuition reimbursement programs. Our compensation and benefits are designed to reward performance and align employee interests with those of our shareholders through equity participation and comprehensive health and financial wellness programs. We also utilize employee listening systems to gather feedback and maintain an inclusive culture where hiring and promotions are based on merit.

---

### 6. `con-024` — citation-anchored, eu-ai-act

**Question.** What extra obligations does Article 55 place on providers of general-purpose AI models with systemic risk?

**Gold label.** `EU AI Act › CHAPTER V › SECTION 3 › Article 55`

**Why that label was chosen.** Art 55(1): in addition to Arts 53 and 54, model evaluation against standardised protocols, assessment and mitigation of systemic risks, incident tracking and reporting, and adequate cybersecurity protection.

**Vector-only found it at rank 1. Hybrid did not return it.**

**Corpus text under that label** — 1 chunk(s):

> **`EU AI Act › CHAPTER V › SECTION 3 › Article 55 — Obligations of providers of general-purpose AI models with systemic risk`**
>
> EU AI Act › CHAPTER V › SECTION 3 › Article 55 — Obligations of providers of general-purpose AI models with systemic risk
> 1. In addition to the obligations listed in Articles 53 and 54, providers of general-purpose AI models with systemic risk shall: (a) perform model evaluation in accordance with standardised protocols and tools reflecting the state of the art, including conducting and documenting adversarial testing of the model with a view to identifying and mitigating systemic risks; (b) assess and mitigate possible systemic risks at Union level, including their sources, that may stem from the development, the placing on the market, or the use of general-purpose AI models with systemic risk; (c) keep track of, document, and report, without undue delay, to the AI Office and, as appropriate, to national competent authorities, relevant information about serious incidents and possible corrective measures to address them; (d) ensure an adequate level of cybersecurity protection for the general-purpose AI model with systemic risk and the physical infrastructure of the model. 2. Providers of general-purpose AI models with systemic risk may rely on codes of practice within the meaning of Article 56 to demonstrate compliance with the obligations set out in paragraph 1 of this Article, until a harmonised standard is published. Compliance with European harmonised standards grants providers the presumption of conformity to the extent that those standards cover those obligations. Providers of general-purpose AI models with systemic risks who do not adhere to an approved code of practice or do not comply with a European harmonised standard shall demonstrate alternative adequate means of compliance for assessment by the Commission. 3. Any information or documentation obtained pursuant to this Article, including trade secrets, shall be treated in accordance with the confidentiality obligations set out in Article 78.

---

### 7. `con-042` — natural-language, eu-ai-act

**Question.** Are regulators allowed to share what they learn about a company's systems?

**Gold label.** `EU AI Act › CHAPTER IX › SECTION 3 › Article 78`

**Why that label was chosen.** Art 78(1): the Commission, market surveillance authorities, notified bodies and any other person involved shall respect the confidentiality of information and data obtained in carrying out their tasks.

**Vector-only found it at rank 1. Hybrid did not return it.**

**Corpus text under that label** — 1 chunk(s):

> **`EU AI Act › CHAPTER IX › SECTION 3 › Article 78 — Confidentiality`**
>
> EU AI Act › CHAPTER IX › SECTION 3 › Article 78 — Confidentiality
> 1. The Commission, market surveillance authorities and notified bodies and any other natural or legal person involved in the application of this Regulation shall, in accordance with Union or national law, respect the confidentiality of information and data obtained in carrying out their tasks and activities in such a manner as to protect, in particular: (a) the intellectual property rights and confidential business information or trade secrets of a natural or legal person, including source code, except in the cases referred to in Article 5 of Directive (EU) 2016/943 of the European Parliament and of the Council ( 57 ) ; (b) the effective implementation of this Regulation, in particular for the purposes of inspections, investigations or audits; (c) public and national security interests; (d) the conduct of criminal or administrative proceedings; (e) information classified pursuant to Union or national law. 2. The authorities involved in the application of this Regulation pursuant to paragraph 1 shall request only data that is strictly necessary for the assessment of the risk posed by AI systems and for the exercise of their powers in accordance with this Regulation and with Regulation (EU) 2019/1020. They shall put in place adequate and effective cybersecurity measures to protect the security and confidentiality of the information and data obtained, and shall delete the data collected as soon as it is no longer needed for the purpose for which it was obtained, in accordance with applicable Union or national law. 3. Without prejudice to paragraphs 1 and 2, information exchanged on a confidential basis between the national competent authorities or between national competent authorities and the Commission shall not be disclosed without prior consultation of the originating national competent authority and the deployer when high-risk AI systems referred to in point 1, 6 or 7 of Annex III are used by law enforcement, border control, immigration or asylum authorities and when such disclosure would jeopardise public and national security interests. This exchange of information shall not cover sensitive operational data in relation to the activities of law enforcement, border control, immigration or asylum authorities. When the law enforcement, immigration or asylum authorities are providers of high-risk AI systems referred to in point 1, 6 or 7 of Annex III, the technical documentation referred to in Annex IV shall remain within the premises of those authorities. Those authorities shall ensure that the market surveillance authorities referred to in Article 74(8) and (9), as applicable, can, upon request, immediately access the documentation or obtain a copy thereof. Only staff of the market surveillance authority holding the appropriate level of security clearance shall be allowed to access that documentation or any copy thereof. 4. Paragraphs 1, 2 and 3 shall not affect the rights or obligations of the Commission, Member States and their relevant authorities, as well as those of notified bodies, with regard to the exchange of information and the dissemination of warnings, including in the context of cross-border cooperation, nor shall they affect the obligations of the parties concerned to provide information under criminal law of the Member States. 5. The Commission and Member States may exchange, where necessary and in accordance with relevant provisions of international and trade agreements, confidential information with regulatory authorities of third countries with which they have concluded bilateral or multilateral confidentiality arrangements guaranteeing an adequate level of confidentiality.

---

### 8. `con-051` — natural-language, nist-ai-rmf

**Question.** How can you tell whether a system actually works as intended and keeps working?

**Gold label.** `NIST AI RMF 1.0 › AI Risks and Trustworthiness › Valid and Reliable`

**Why that label was chosen.** Validation is 'confirmation, through the provision of objective evidence, that the requirements for a specific intended use or application have been fulfilled'; reliability is the ability to perform as required, without failure, for a given time interval.

**Vector-only found it at rank 1. Hybrid did not return it.**

**Corpus text under that label** — 1 chunk(s):

> **`NIST AI RMF 1.0 › AI Risks and Trustworthiness › Valid and Reliable`**
>
> NIST AI RMF 1.0 › AI Risks and Trustworthiness › Valid and Reliable
> Validation is the “confirmation, through the provision of objective evidence, that the requirements for a specific intended use or application have been fulfilled” (Source: ISO 9000:2015). Deployment of AI systems which are inaccurate, unreliable, or poorly generalized to data and settings beyond their training creates and increases negative AI risks and reduces trustworthiness. Reliability is defined in the same standard as the “ability of an item to perform as required, without failure, for a given time interval, under given conditions” (Source: ISO /IEC TS 5723:2022). Reliability is a goal for overall correctness of AI system operation under the conditions of expected use and over a given period of time, including the entire lifetime of the system. Page 13 Accuracy and robustness contribute to the validity and trustworthiness of AI systems, and can be in tension with one another in AI systems. Accuracy is defined by ISO /IEC TS 5723:2022 as “closeness of results of observations, computations, or estimates to the true values or the values accepted as being true.” Measures of accuracy should consider computational-centric measures (e.g., false positive and false negative rates), human-AI teaming, and demonstrate external validity (generalizable beyond the training conditions). Accuracy measurements should always be paired with clearly defined and realistic test sets – that are representative of conditions of expected use – and details about test methodology; these should be included in associated documentation. Accuracy measurements may include disaggregation of results for different data segments. Robustness or generalizability is defined as the “ability of a system to maintain its level of performance under a variety of circumstances” (Source: ISO /IEC TS 5723:2022). Robustness is a goal for appropriate system functionality in a broad set of conditions and circumstances, including uses of AI systems not initially anticipated. Robustness requires not only that the system perform exactly as it does under expected uses, but also that it should perform in ways that minimize potential harms to people if it is operating in an unexpected setting. Validity and reliability for deployed AI systems are often assessed by ongoing testing or monitoring that confirms a system is performing as intended. Measurement of validity, accuracy, robustness, and reliability contribute to trustworthiness and should take into consideration that certain types of failures can cause greater harm. AI risk management efforts should prioritize the minimization of potential negative impacts, and may need to include human intervention in cases where the AI system cannot detect or correct errors.

---

### 9. `con-063` — natural-language, eu-ai-act

**Question.** Does the state of the art matter when judging whether a system complies?

**Gold label.** `EU AI Act › CHAPTER III › SECTION 2 › Article 8`

**Why that label was chosen.** Art 8(1): high-risk systems shall comply with the requirements of this Section taking into account their intended purpose as well as the generally acknowledged state of the art on AI and AI-related technologies.

**Vector-only found it at rank 4. Hybrid did not return it.**

**Corpus text under that label** — 1 chunk(s):

> **`EU AI Act › CHAPTER III › SECTION 2 › Article 8 — Compliance with the requirements`**
>
> EU AI Act › CHAPTER III › SECTION 2 › Article 8 — Compliance with the requirements
> 1. High-risk AI systems shall comply with the requirements laid down in this Section, taking into account their intended purpose as well as the generally acknowledged state of the art on AI and AI-related technologies. The risk management system referred to in Article 9 shall be taken into account when ensuring compliance with those requirements. 2. Where a product contains an AI system, to which the requirements of this Regulation as well as requirements of the Union harmonisation legislation listed in Section A of Annex I apply, providers shall be responsible for ensuring that their product is fully compliant with all applicable requirements under applicable Union harmonisation legislation. In ensuring the compliance of high-risk AI systems referred to in paragraph 1 with the requirements set out in this Section, and in order to ensure consistency, avoid duplication and minimise additional burdens, providers shall have a choice of integrating, as appropriate, the necessary testing and reporting processes, information and documentation they provide with regard to their product into documentation and procedures that already exist and are required under the Union harmonisation legislation listed in Section A of Annex I.

---

### 10. `con-064` — natural-language, eu-ai-act

**Question.** A model developer based outside Europe wants to offer its model there — does it need someone inside the EU?

**Gold label.** `EU AI Act › CHAPTER V › SECTION 2 › Article 54`

**Why that label was chosen.** Art 54(1): prior to placing a general-purpose AI model on the Union market, providers established in third countries shall, by written mandate, appoint an authorised representative established in the Union.

**Vector-only found it at rank 1. Hybrid did not return it.**

**Corpus text under that label** — 1 chunk(s):

> **`EU AI Act › CHAPTER V › SECTION 2 › Article 54 — Authorised representatives of providers of general-purpose AI models`**
>
> EU AI Act › CHAPTER V › SECTION 2 › Article 54 — Authorised representatives of providers of general-purpose AI models
> 1. Prior to placing a general-purpose AI model on the Union market, providers established in third countries shall, by written mandate, appoint an authorised representative which is established in the Union. 2. The provider shall enable its authorised representative to perform the tasks specified in the mandate received from the provider. 3. The authorised representative shall perform the tasks specified in the mandate received from the provider. It shall provide a copy of the mandate to the AI Office upon request, in one of the official languages of the institutions of the Union. For the purposes of this Regulation, the mandate shall empower the authorised representative to carry out the following tasks: (a) verify that the technical documentation specified in Annex XI has been drawn up and all obligations referred to in Article 53 and, where applicable, Article 55 have been fulfilled by the provider; (b) keep a copy of the technical documentation specified in Annex XI at the disposal of the AI Office and national competent authorities, for a period of 10 years after the general-purpose AI model has been placed on the market, and the contact details of the provider that appointed the authorised representative; (c) provide the AI Office, upon a reasoned request, with all the information and documentation, including that referred to in point (b), necessary to demonstrate compliance with the obligations in this Chapter; (d) cooperate with the AI Office and competent authorities, upon a reasoned request, in any action they take in relation to the general-purpose AI model, including when the model is integrated into AI systems placed on the market or put into service in the Union. 4. The mandate shall empower the authorised representative to be addressed, in addition to or instead of the provider, by the AI Office or the competent authorities, on all issues related to ensuring compliance with this Regulation. 5. The authorised representative shall terminate the mandate if it considers or has reason to consider the provider to be acting contrary to its obligations pursuant to this Regulation. In such a case, it shall also immediately inform the AI Office about the termination of the mandate and the reasons therefor. 6. The obligation set out in this Article shall not apply to providers of general-purpose AI models that are released under a free and open-source licence that allows for the access, usage, modification, and distribution of the model, and whose parameters, including the weights, the information on the model architecture, and the information on model usage, are made publicly available, unless the general-purpose AI models present systemic risks.

---

### 11. `con-067` — natural-language, eu-ai-act

**Question.** Is there anything encouraging companies to apply these rules voluntarily to systems that are not high-risk?

**Gold label.** `EU AI Act › CHAPTER X › Article 95`

**Why that label was chosen.** Art 95: the AI Office and the Member States shall encourage and facilitate the drawing up of codes of conduct intended to foster voluntary application of the Chapter III Section 2 requirements to systems other than high-risk ones.

**Vector-only found it at rank 5. Hybrid did not return it.**

**Corpus text under that label** — 1 chunk(s):

> **`EU AI Act › CHAPTER X › Article 95 — Codes of conduct for voluntary application of specific requirements – Article 96 — Guidelines from the Commission on the implementation of this Regulation`**
>
> EU AI Act › CHAPTER X › Article 95 — Codes of conduct for voluntary application of specific requirements – Article 96 — Guidelines from the Commission on the implementation of this Regulation
> 1. The AI Office and the Member States shall encourage and facilitate the drawing up of codes of conduct, including related governance mechanisms, intended to foster the voluntary application to AI systems, other than high-risk AI systems, of some or all of the requirements set out in Chapter III, Section 2 taking into account the available technical solutions and industry best practices allowing for the application of such requirements. 2. The AI Office and the Member States shall facilitate the drawing up of codes of conduct concerning the voluntary application, including by deployers, of specific requirements to all AI systems, on the basis of clear objectives and key performance indicators to measure the achievement of those objectives, including elements such as, but not limited to: (a) applicable elements provided for in Union ethical guidelines for trustworthy AI; (b) assessing and minimising the impact of AI systems on environmental sustainability, including as regards energy-efficient programming and techniques for the efficient design, training and use of AI; (c) promoting AI literacy, in particular that of persons dealing with the development, operation and use of AI; (d) facilitating an inclusive and diverse design of AI systems, including through the establishment of inclusive and diverse development teams and the promotion of stakeholders’ participation in that process; (e) assessing and preventing the negative impact of AI systems on vulnerable persons or groups of vulnerable persons, including as regards accessibility for persons with a disability, as well as on gender equality. 3. Codes of conduct may be drawn up by individual providers or deployers of AI systems or by organisations representing them or by both, including with the involvement of any interested stakeholders and their representative organisations, including civil society organisations and academia. Codes of conduct may cover one or more AI systems taking into account the similarity of the intended purpose of the relevant systems. 4. The AI Office and the Member States shall take into account the specific interests and needs of SMEs, including start-ups, when encouraging and facilitating the drawing up of codes of conduct. 1. The Commission shall develop guidelines on the practical implementation of this Regulation, and in particular on: (a) the application of the requirements and obligations referred to in Articles 8 to 15 and in Article 25; (b) the prohibited practices referred to in Article 5; (c) the practical implementation of the provisions related to substantial modification; (d) the practical implementation of transparency obligations laid down in Article 50; (e) detailed information on the relationship of this Regulation with the Union harmonisation legislation listed in Annex I, as well as with other relevant Union law, including as regards consistency in their enforcement; (f) the application of the definition of an AI system as set out in Article 3, point (1). When issuing such guidelines, the Commission shall pay particular attention to the needs of SMEs including start-ups, of local public authorities and of the sectors most likely to be affected by this Regulation. The guidelines referred to in the first subparagraph of this paragraph shall take due account of the generally acknowledged state of the art on AI, as well as of relevant harmonised standards and common specifications that are referred to in Articles 40 and 41, or of those harmonised standards or technical specifications that are set out pursuant to Union harmonisation law. 2. At the request of the Member States or the AI Office, or on its own initiative, the Commission shall update guidelines previously adopted when deemed necessary.

---

### 12. `con-070` — natural-language, eu-ai-act

**Question.** Is there any relief for very small companies?

**Gold label.** `EU AI Act › CHAPTER VI › Article 63`

**Why that label was chosen.** Art 63(1): microenterprises within the meaning of Recommendation 2003/361/EC may comply with certain elements of the required quality management system in a simplified manner.

**Vector-only found it at rank 2. Hybrid did not return it.**

**Corpus text under that label** — 1 chunk(s):

> **`EU AI Act › CHAPTER VI › Article 63 — Derogations for specific operators`**
>
> EU AI Act › CHAPTER VI › Article 63 — Derogations for specific operators
> 1. Microenterprises within the meaning of Recommendation 2003/361/EC may comply with certain elements of the quality management system required by Article 17 of this Regulation in a simplified manner, provided that they do not have partner enterprises or linked enterprises within the meaning of that Recommendation. For that purpose, the Commission shall develop guidelines on the elements of the quality management system which may be complied with in a simplified manner considering the needs of microenterprises, without affecting the level of protection or the need for compliance with the requirements in respect of high-risk AI systems. 2. Paragraph 1 of this Article shall not be interpreted as exempting those operators from fulfilling any other requirements or obligations laid down in this Regulation, including those established in Articles 9, 10, 11, 12, 13, 14, 15, 72 and 73.

---

### 13. `con-075` — natural-language, nvidia-10k

**Question.** What risk does the company see from the partners it has struck deals with?

**Gold label.** `NVIDIA 10-K FY2026 › Item 1A. Risk Factors › Commercial arrangements`

**Why that label was chosen.** 'Commercial arrangements expose us to counterparty risks' — long-term capacity purchase obligations and financial guarantees, and the exposure if a counterparty fails to perform.

**Vector-only found it at rank 3. Hybrid did not return it.**

**Corpus text under that label** — 2 chunk(s):

> **`NVIDIA 10-K FY2026 › Item 1A. Risk Factors › Commercial arrangements expose us to counterparty risks.`**
>
> NVIDIA 10-K FY2026 › Item 1A. Risk Factors › Commercial arrangements expose us to counterparty risks.
> We have entered and may in the future enter into commercial arrangements, including long-term capacity purchase obligations and financial guarantees, and have been asked to offer financing arrangements to support our customers’ and partners’ buildout of datacenter infrastructure. We have not entered into any financing arrangements. Commercial arrangements expose us to counterparty risk, including customers' or partners' inability to fulfill their financial commitments and secure necessary financing or infrastructure, the occurrence of significant project delays, and counterparty financial distress or insolvency, all of which may negatively impact our business, financial condition, or results of operations. Financing arrangements, if undertaken, may in some circumstances result in lower upfront cash flows associated with extended payment terms or payment terms made over a multi-year term and may increase credit risk. If we are unable to attract, retain and motivate our executives and key employees, our business may be harmed. To remain competitive and successfully execute our business strategy, we must attract, retain, and motivate our executives and key employees, as well as recruit and develop exceptional talent. However, labor is subject to external factors that are beyond our control, including our industry’s increasingly highly competitive market for skilled workers and leaders, and workforce participation rates. Changes in immigration and work permit regulations, or in their administration or interpretation, could impair our ability to attract, employ and retain qualified employees. Competition for talent drives up costs in the form of cash and stock-based compensation. In times of stock price volatility, as we have experienced in the past and may experience in the future, the retentive value of our stock-based compensation may decrease. Additionally, we are highly dependent on the services of our longstanding executive team. Failure to ensure effective succession planning, transfer of knowledge, and smooth transitions involving executives and key employees could hinder our strategic planning, execution, and long-term success. 23 Table of Contents Our business is dependent upon the proper functioning of our business processes and information systems and modification or interruption of such systems may disrupt our business and internal controls. We rely upon internal processes and information systems to support key business functions, including our assessment of internal controls over financial reporting as required by Section 404 of the Sarbanes-Oxley Act. The efficient operation and scalability of these processes and systems is critical to support our growth. We continue to design and implement updated accounting functionality related to a new enterprise resource planning, or ERP, system. Any ERP system implementation may introduce problems, such as quality issues or programming errors, that could have an impact on our continued ability to successfully operate our business or to timely and accurately report our financial results. These changes may be costly and disruptive to our operations and could impose substantial demands on management time. Failure to implement new or updated controls, or difficulties encountered in their implementation, could harm our operating results or cause us to fail to meet our reporting obligations. Identification of material weaknesses in our internal controls, even if quickly remediated once disclosed, may cause investors to lose confidence in our financial statements and our stock price may decline. Remediation of any material weakness could require us to incur significant expenses, and if we fail to remediate any material weakness, our financial statements may be inaccurate, we may be required to restate our financial statements, our ability to report our financial results on a timely and accurate basis may be adversely affected, our access to the capital markets may be restricted, our stock price may decline, and we may be subject to sanctions or investigation by regulatory authorities. Our operating results have in the past fluctuated and may in the future fluctuate, and if our operating results are below the expectations of securities analysts or investors, our stock price could decline. Our operating results have in the past fluctuated and may continue to fluctuate due to a number of factors.

> **`NVIDIA 10-K FY2026 › Item 1A. Risk Factors › Commercial arrangements expose us to counterparty risks.`**
>
> NVIDIA 10-K FY2026 › Item 1A. Risk Factors › Commercial arrangements expose us to counterparty risks.
> Remediation of any material weakness could require us to incur significant expenses, and if we fail to remediate any material weakness, our financial statements may be inaccurate, we may be required to restate our financial statements, our ability to report our financial results on a timely and accurate basis may be adversely affected, our access to the capital markets may be restricted, our stock price may decline, and we may be subject to sanctions or investigation by regulatory authorities. Our operating results have in the past fluctuated and may in the future fluctuate, and if our operating results are below the expectations of securities analysts or investors, our stock price could decline. Our operating results have in the past fluctuated and may continue to fluctuate due to a number of factors. Therefore, investors should not rely on our past results of operations as an indication of our future performance. Factors that could affect our results of operations include, but are not limited to: • our ability to adjust spending due to the multi-year development cycle for some of our products and services; • our ability to comply with our contractual obligations to customers; • our extended payment term arrangements with certain customers, the inability of some customers to make required payments, our ability to obtain credit insurance for customers with extended payment terms, and customer bad debt write-offs; • our vendors' payment requirements; • unanticipated costs associated with environmental liabilities; and • changes in financial accounting standards or interpretations of existing standards. Any of these factors could prevent us from achieving our anticipated financial results. For example, we have granted and may continue to grant extended payment terms to some customers, particularly during macroeconomic downturns, which could impact our ability to collect payment. Our vendors have requested and may continue to ask for shorter payment terms, which may impact our cash flow generation. These arrangements reduce the cash we have available for general business operations. In addition, the pace of growth in our operating expenses and investments may lag our revenue growth, creating volatility or periods where profitability levels may not be sustainable. Failure to meet our expectations or the expectations of our investors or security analysts is likely to cause our stock price to decline, as it has in the past, or substantial price volatility.

---

### 14. `con-077` — natural-language, nvidia-10k

**Question.** How much has the company set aside for faulty products?

**Gold label.** `NVIDIA 10-K FY2026 › Item 15. Exhibits and Financial Statement Schedules › Notes to the Consolidated Financial Statements – Accrual for Product Warranty`

**Why that label was chosen.** 'The estimated amount of product warranty liabilities was $2.8 billion and $1.3 billion as of January 25, 2026 and January 26, 2025, respectively.'

**Vector-only found it at rank 1. Hybrid did not return it.**

**Corpus text under that label** — 1 chunk(s):

> **`NVIDIA 10-K FY2026 › Item 15. Exhibits and Financial Statement Schedules › Notes to the Consolidated Financial Statements – Accrual for Product Warranty Liabilities`**
>
> NVIDIA 10-K FY2026 › Item 15. Exhibits and Financial Statement Schedules › Notes to the Consolidated Financial Statements – Accrual for Product Warranty Liabilities
> (Continued) The estimated amount of product warranty liabilities was $ 2.8 billion and $ 1.3 billion as of January 25, 2026 and January 26, 2025, respectively. The estimated product returns and product warranty activity consisted of the following: Year Ended Jan 25, 2026 | Jan 26, 2025 | Jan 28, 2024 (In millions) Balance at beginning of period | $ | 1,290 | $ | 306 | $ | 82 Additions | 2,474 | 1,203 | 278 Utilization | ( 957 ) | ( 219 ) | ( 54 ) Balance at end of period | $ | 2,807 | $ | 1,290 | $ | 306 In fiscal years 2026, 2025, and 2024 the additions in product warranty liabilities primarily related to our Compute & Networking segment. We have provided indemnities for matters such as tax, product, and employee liabilities. We have included intellectual property indemnification provisions in our technology-related agreements with third parties. Maximum potential future payments cannot be estimated because many of these agreements do not have a maximum stated liability. We have not recorded any liability in our Consolidated Financial Statements for such indemnifications.

---

### 15. `con-080` — natural-language, nist-ai-rmf

**Question.** Does anyone know yet whether following this framework actually makes systems more trustworthy?

**Gold label.** `NIST AI RMF 1.0 › Effectiveness of the AI RMF`

**Why that label was chosen.** 'Evaluations of AI RMF effectiveness – including ways to measure bottom-line improvements in the trustworthiness of AI systems – will be part of future NIST activities'.

**Vector-only found it at rank 5. Hybrid did not return it.**

**Corpus text under that label** — 1 chunk(s):

> **`NIST AI RMF 1.0 › Effectiveness of the AI RMF`**
>
> NIST AI RMF 1.0 › Effectiveness of the AI RMF
> Evaluations of AI RMF effectiveness – including ways to measure bottom-line improvements in the trustworthiness of AI systems – will be part of future NIST activities, in conjunction with the AI community. Organizations and other users of the Framework are encouraged to periodically evaluate whether the AI RMF has improved their ability to manage AI risks, including but not limited to their policies, processes, practices, implementation plans, indicators, measurements, and expected outcomes. NIST intends to work collaboratively with others to develop metrics, methodologies, and goals for evaluating the AI RMF’s effectiveness, and to broadly share results and supporting information. Framework users are expected to benefit from: • enhanced processes for governing, mapping, measuring, and managing AI risk, and clearly documenting outcomes; • improved awareness of the relationships and tradeoffs among trustworthiness characteristics, socio-technical approaches, and AI risks; • explicit processes for making go/no-go system commissioning and deployment decisions; • established policies, processes, practices, and procedures for improving organizational accountability efforts related to AI system risks; • enhanced organizational culture which prioritizes the identification and management of AI system risks and potential impacts to individuals, communities, organizations, and society; • better information sharing within and across organizations about risks, decisionmaking processes, responsibilities, common pitfalls, TEVV practices, and approaches for continuous improvement; • greater contextual knowledge for increased awareness of downstream risks; • strengthened engagement with interested parties and relevant AI actors; and • augmented capacity for TEVV of AI systems and associated risks. Page 19

---

### 16. `con-093` — natural-language, eu-ai-act

**Question.** Can a body that protects people's rights get hold of documentation about a system?

**Gold label.** `EU AI Act › CHAPTER IX › SECTION 3 › Article 77`

**Why that label was chosen.** Art 77(1): national authorities supervising obligations under Union law protecting fundamental rights, including non-discrimination, have the power to request and access any documentation created or maintained under this Regulation.

**Vector-only found it at rank 1. Hybrid did not return it.**

**Corpus text under that label** — 1 chunk(s):

> **`EU AI Act › CHAPTER IX › SECTION 3 › Article 77 — Powers of authorities protecting fundamental rights`**
>
> EU AI Act › CHAPTER IX › SECTION 3 › Article 77 — Powers of authorities protecting fundamental rights
> 1. National public authorities or bodies which supervise or enforce the respect of obligations under Union law protecting fundamental rights, including the right to non-discrimination, in relation to the use of high-risk AI systems referred to in Annex III shall have the power to request and access any documentation created or maintained under this Regulation in accessible language and format when access to that documentation is necessary for effectively fulfilling their mandates within the limits of their jurisdiction. The relevant public authority or body shall inform the market surveillance authority of the Member State concerned of any such request. 2. By 2 November 2024, each Member State shall identify the public authorities or bodies referred to in paragraph 1 and make a list of them publicly available. Member States shall notify the list to the Commission and to the other Member States, and shall keep the list up to date. 3. Where the documentation referred to in paragraph 1 is insufficient to ascertain whether an infringement of obligations under Union law protecting fundamental rights has occurred, the public authority or body referred to in paragraph 1 may make a reasoned request to the market surveillance authority, to organise testing of the high-risk AI system through technical means. The market surveillance authority shall organise the testing with the close involvement of the requesting public authority or body within a reasonable time following the request. 4. Any information or documentation obtained by the national public authorities or bodies referred to in paragraph 1 of this Article pursuant to this Article shall be treated in accordance with the confidentiality obligations set out in Article 78.

---

### 17. `con-096` — natural-language, eu-ai-act

**Question.** Who decides how these controlled testing environments actually work day to day?

**Gold label.** `EU AI Act › CHAPTER VI › Article 58`

**Why that label was chosen.** Art 58(1): to avoid fragmentation across the Union, the Commission shall adopt implementing acts specifying the detailed arrangements for the establishment, development, implementation, operation and supervision of the sandboxes.

**Vector-only found it at rank 6. Hybrid did not return it.**

**Corpus text under that label** — 2 chunk(s):

> **`EU AI Act › CHAPTER VI › Article 58 — Detailed arrangements for, and functioning of, AI regulatory sandboxes`**
>
> EU AI Act › CHAPTER VI › Article 58 — Detailed arrangements for, and functioning of, AI regulatory sandboxes
> 1. In order to avoid fragmentation across the Union, the Commission shall adopt implementing acts specifying the detailed arrangements for the establishment, development, implementation, operation and supervision of the AI regulatory sandboxes. The implementing acts shall include common principles on the following issues: (a) eligibility and selection criteria for participation in the AI regulatory sandbox; (b) procedures for the application, participation, monitoring, exiting from and termination of the AI regulatory sandbox, including the sandbox plan and the exit report; (c) the terms and conditions applicable to the participants. Those implementing acts shall be adopted in accordance with the examination procedure referred to in Article 98(2). 2. The implementing acts referred to in paragraph 1 shall ensure: (a) that AI regulatory sandboxes are open to any applying provider or prospective provider of an AI system who fulfils eligibility and selection criteria, which shall be transparent and fair, and that national competent authorities inform applicants of their decision within three months of the application; (b) that AI regulatory sandboxes allow broad and equal access and keep up with demand for participation; providers and prospective providers may also submit applications in partnerships with deployers and other relevant third parties; (c) that the detailed arrangements for, and conditions concerning AI regulatory sandboxes support, to the best extent possible, flexibility for national competent authorities to establish and operate their AI regulatory sandboxes; (d) that access to the AI regulatory sandboxes is free of charge for SMEs, including start-ups, without prejudice to exceptional costs that national competent authorities may recover in a fair and proportionate manner; (e) that they facilitate providers and prospective providers, by means of the learning outcomes of the AI regulatory sandboxes, in complying with conformity assessment obligations under this Regulation and the voluntary application of the codes of conduct referred to in Article 95; (f) that AI regulatory sandboxes facilitate the involvement of other relevant actors within the AI ecosystem, such as notified bodies and standardisation organisations, SMEs, including start-ups, enterprises, innovators, testing and experimentation facilities, research and experimentation labs and European Digital Innovation Hubs, centres of excellence, individual researchers, in order to allow and facilitate cooperation with the public and private sectors; (g) that procedures, processes and administrative requirements for application, selection, participation and exiting the AI regulatory sandbox are simple, easily intelligible, and clearly communicated in order to facilitate the participation of SMEs, including start-ups, with limited legal and administrative capacities and are streamlined across the Union, in order to avoid fragmentation and that participation in an AI regulatory sandbox established by a Member State, or by the European Data Protection Supervisor is mutually and uniformly recognised and carries the same legal effects across the Union; (h) that participation in the AI regulatory sandbox is limited to a period that is appropriate to the complexity and scale of the project and that may be extended by the national competent authority; (i) that AI regulatory sandboxes facilitate the development of tools and infrastructure for testing, benchmarking, assessing and explaining dimensions of AI systems relevant for regulatory learning, such as accuracy, robustness and cybersecurity, as well as measures to mitigate risks to fundamental rights and society at large. 3. Prospective providers in the AI regulatory sandboxes, in particular SMEs and start-ups, shall be directed, where relevant, to pre-deployment services such as guidance on the implementation of this Regulation, to other value-adding services such as help with standardisation documents and certification, testing and experimentation facilities, European Digital Innovation Hubs and centres of excellence.

> **`EU AI Act › CHAPTER VI › Article 58 — Detailed arrangements for, and functioning of, AI regulatory sandboxes`**
>
> EU AI Act › CHAPTER VI › Article 58 — Detailed arrangements for, and functioning of, AI regulatory sandboxes
> (i) that AI regulatory sandboxes facilitate the development of tools and infrastructure for testing, benchmarking, assessing and explaining dimensions of AI systems relevant for regulatory learning, such as accuracy, robustness and cybersecurity, as well as measures to mitigate risks to fundamental rights and society at large. 3. Prospective providers in the AI regulatory sandboxes, in particular SMEs and start-ups, shall be directed, where relevant, to pre-deployment services such as guidance on the implementation of this Regulation, to other value-adding services such as help with standardisation documents and certification, testing and experimentation facilities, European Digital Innovation Hubs and centres of excellence. 4. Where national competent authorities consider authorising testing in real world conditions supervised within the framework of an AI regulatory sandbox to be established under this Article, they shall specifically agree the terms and conditions of such testing and, in particular, the appropriate safeguards with the participants, with a view to protecting fundamental rights, health and safety. Where appropriate, they shall cooperate with other national competent authorities with a view to ensuring consistent practices across the Union.

---

### 18. `con-100` — natural-language, eu-ai-act

**Question.** Can a system ever be allowed onto the market without going through the usual assessment?

**Gold label.** `EU AI Act › CHAPTER III › SECTION 5 › Article 46`

**Why that label was chosen.** Art 46(1): a market surveillance authority may authorise the placing on the market of specific high-risk systems for exceptional reasons of public security or the protection of life and health, environmental protection, or key industrial and infrastructural assets.

**Vector-only found it at rank 2. Hybrid did not return it.**

**Corpus text under that label** — 1 chunk(s):

> **`EU AI Act › CHAPTER III › SECTION 5 › Article 46 — Derogation from conformity assessment procedure`**
>
> EU AI Act › CHAPTER III › SECTION 5 › Article 46 — Derogation from conformity assessment procedure
> 1. By way of derogation from Article 43 and upon a duly justified request, any market surveillance authority may authorise the placing on the market or the putting into service of specific high-risk AI systems within the territory of the Member State concerned, for exceptional reasons of public security or the protection of life and health of persons, environmental protection or the protection of key industrial and infrastructural assets. That authorisation shall be for a limited period while the necessary conformity assessment procedures are being carried out, taking into account the exceptional reasons justifying the derogation. The completion of those procedures shall be undertaken without undue delay. 2. In a duly justified situation of urgency for exceptional reasons of public security or in the case of specific, substantial and imminent threat to the life or physical safety of natural persons, law-enforcement authorities or civil protection authorities may put a specific high-risk AI system into service without the authorisation referred to in paragraph 1, provided that such authorisation is requested during or after the use without undue delay. If the authorisation referred to in paragraph 1 is refused, the use of the high-risk AI system shall be stopped with immediate effect and all the results and outputs of such use shall be immediately discarded. 3. The authorisation referred to in paragraph 1 shall be issued only if the market surveillance authority concludes that the high-risk AI system complies with the requirements of Section 2. The market surveillance authority shall inform the Commission and the other Member States of any authorisation issued pursuant to paragraphs 1 and 2. This obligation shall not cover sensitive operational data in relation to the activities of law-enforcement authorities. 4. Where, within 15 calendar days of receipt of the information referred to in paragraph 3, no objection has been raised by either a Member State or the Commission in respect of an authorisation issued by a market surveillance authority of a Member State in accordance with paragraph 1, that authorisation shall be deemed justified. 5. Where, within 15 calendar days of receipt of the notification referred to in paragraph 3, objections are raised by a Member State against an authorisation issued by a market surveillance authority of another Member State, or where the Commission considers the authorisation to be contrary to Union law, or the conclusion of the Member States regarding the compliance of the system as referred to in paragraph 3 to be unfounded, the Commission shall, without delay, enter into consultations with the relevant Member State. The operators concerned shall be consulted and have the possibility to present their views. Having regard thereto, the Commission shall decide whether the authorisation is justified. The Commission shall address its decision to the Member State concerned and to the relevant operators. 6. Where the Commission considers the authorisation unjustified, it shall be withdrawn by the market surveillance authority of the Member State concerned. 7. For high-risk AI systems related to products covered by Union harmonisation legislation listed in Section A of Annex I, only the derogations from the conformity assessment established in that Union harmonisation legislation shall apply.

---

### 19. `con-101` — natural-language, eu-ai-act

**Question.** Is there any support for smaller firms trying to comply?

**Gold label.** `EU AI Act › CHAPTER VI › Article 61`

**Why that label was chosen.** Art 62, sharing a chunk with Art 61: Member States shall provide SMEs and start-ups with priority access to sandboxes, awareness-raising activities, and dedicated communication channels.

**Vector-only found it at rank 2. Hybrid did not return it.**

**Corpus text under that label** — 1 chunk(s):

> **`EU AI Act › CHAPTER VI › Article 61 — Informed consent to participate in testing in real world conditions outside AI regulatory sandboxes – Article 62 — Measures for providers and deployers, in particular SMEs, including start-ups`**
>
> EU AI Act › CHAPTER VI › Article 61 — Informed consent to participate in testing in real world conditions outside AI regulatory sandboxes – Article 62 — Measures for providers and deployers, in particular SMEs, including start-ups
> 1. For the purpose of testing in real world conditions under Article 60, freely-given informed consent shall be obtained from the subjects of testing prior to their participation in such testing and after their having been duly informed with concise, clear, relevant, and understandable information regarding: (a) the nature and objectives of the testing in real world conditions and the possible inconvenience that may be linked to their participation; (b) the conditions under which the testing in real world conditions is to be conducted, including the expected duration of the subject or subjects’ participation; (c) their rights, and the guarantees regarding their participation, in particular their right to refuse to participate in, and the right to withdraw from, testing in real world conditions at any time without any resulting detriment and without having to provide any justification; (d) the arrangements for requesting the reversal or the disregarding of the predictions, recommendations or decisions of the AI system; (e) the Union-wide unique single identification number of the testing in real world conditions in accordance with Article 60(4) point (c), and the contact details of the provider or its legal representative from whom further information can be obtained. 2. The informed consent shall be dated and documented and a copy shall be given to the subjects of testing or their legal representative. 1. Member States shall undertake the following actions: (a) provide SMEs, including start-ups, having a registered office or a branch in the Union, with priority access to the AI regulatory sandboxes, to the extent that they fulfil the eligibility conditions and selection criteria; the priority access shall not preclude other SMEs, including start-ups, other than those referred to in this paragraph from access to the AI regulatory sandbox, provided that they also fulfil the eligibility conditions and selection criteria; (b) organise specific awareness raising and training activities on the application of this Regulation tailored to the needs of SMEs including start-ups, deployers and, as appropriate, local public authorities; (c) utilise existing dedicated channels and where appropriate, establish new ones for communication with SMEs including start-ups, deployers, other innovators and, as appropriate, local public authorities to provide advice and respond to queries about the implementation of this Regulation, including as regards participation in AI regulatory sandboxes; (d) facilitate the participation of SMEs and other relevant stakeholders in the standardisation development process. 2. The specific interests and needs of the SME providers, including start-ups, shall be taken into account when setting the fees for conformity assessment under Article 43, reducing those fees proportionately to their size, market size and other relevant indicators. 3. The AI Office shall undertake the following actions: (a) provide standardised templates for areas covered by this Regulation, as specified by the Board in its request; (b) develop and maintain a single information platform providing easy to use information in relation to this Regulation for all operators across the Union; (c) organise appropriate communication campaigns to raise awareness about the obligations arising from this Regulation; (d) evaluate and promote the convergence of best practices in public procurement procedures in relation to AI systems.

---

### 20. `con-120` — cross-section, nist-ai-rmf

**Question.** What does it mean for a system to be understandable to the person using it, and where is that listed among the qualities a trustworthy system needs?

**Gold label.** `NIST AI RMF 1.0 › AI Risks and Trustworthiness › Explainable`

**Also contains (the other half of the span).** `NIST AI RMF 1.0 › AI RMF Core › Measure`

**Why that label was chosen.** The Explainable and Interpretable characteristic distinguishes the mechanism of operation from the meaning of outputs in context; MEASURE is where trustworthiness characteristics are assessed and tracked. Gold is the characteristic section, since 'where is it listed among the qualities' is the more reachable half.

**Vector-only found it at rank 3. Hybrid did not return it.**

**Corpus text under that label** — 1 chunk(s):

> **`NIST AI RMF 1.0 › AI Risks and Trustworthiness › Explainable and Interpretable – Privacy-Enhanced`**
>
> NIST AI RMF 1.0 › AI Risks and Trustworthiness › Explainable and Interpretable – Privacy-Enhanced
> Explainability refers to a representation of the mechanisms underlying AI systems’ operation, whereas interpretability refers to the meaning of AI systems’ output in the context of their designed functional purposes. Together, explainability and interpretability assist those operating or overseeing an AI system, as well as users of an AI system, to gain deeper insights into the functionality and trustworthiness of the system, including its outputs. The underlying assumption is that perceptions of negative risk stem from a lack of ability to make sense of, or contextualize, system output appropriately. Explainable and interpretable AI systems offer information that will help end users understand the purposes and potential impact of an AI system. Risk from lack of explainability may be managed by describing how AI systems function, with descriptions tailored to individual differences such as the user’s role, knowledge, and skill level. Explainable systems can be debugged and monitored more easily, and they lend themselves to more thorough documentation, audit, and governance. Page 16 Risks to interpretability often can be addressed by communicating a description of why an AI system made a particular prediction or recommendation. (See “Four Principles of Explainable Artificial Intelligence” and “Psychological Foundations of Explainability and Interpretability in Artificial Intelligence” found here.) Transparency, explainability, and interpretability are distinct characteristics that support each other. Transparency can answer the question of “what happened” in the system. Explainability can answer the question of “how” a decision was made in the system. Interpretability can answer the question of “why” a decision was made by the system and its meaning or context to the user. Privacy refers generally to the norms and practices that help to safeguard human autonomy, identity, and dignity. These norms and practices typically address freedom from intrusion, limiting observation, or individuals’ agency to consent to disclosure or control of facets of their identities (e.g., body, data, reputation). (See The NIST Privacy Framework: A Tool for Improving Privacy through Enterprise Risk Management.) Privacy values such as anonymity, confidentiality, and control generally should guide choices for AI system design, development, and deployment. Privacy-related risks may influence security, bias, and transparency and come with tradeoffs with these other characteristics. Like safety and security, specific technical features of an AI system may promote or reduce privacy. AI systems can also present new risks to privacy by allowing inference to identify individuals or previously private information about individuals. Privacy-enhancing technologies (“PETs”) for AI, as well as data minimizing methods such as de-identification and aggregation for certain model outputs, can support design for privacy-enhanced AI systems. Under certain conditions such as data sparsity, privacyenhancing techniques can result in a loss in accuracy, affecting decisions about fairness and other values in certain domains.

---

## Part 2 — the 3 cases hybrid found and vector-only did not (`b`)

Listed for completeness; they are the other side of the same 23.

- `con-004` (natural-language, eu-ai-act) — *What quality standards apply to the data used to train a high-risk system?* → `EU AI Act › CHAPTER III › SECTION 2 › Article 10`, hybrid rank 6
- `con-094` (natural-language, eu-ai-act) — *Can anyone actually test a general-purpose model to see whether it behaves?* → `EU AI Act › CHAPTER IX › SECTION 5 › Article 92`, hybrid rank 5
- `con-106` (natural-language, nvidia-10k) — *How does the company work out earnings per share?* → `NVIDIA 10-K FY2026 › Item 15. Exhibits and Financial Statement Schedules › Note 4 - Net Income Per Share`, hybrid rank 6

---

## Appendix — what hybrid returned instead

**Read this after judging the golds, not before.** Seeing the competing section first anchors the judgement toward *"hybrid's answer was also reasonable"*, which is a real question and a different one from *"is this gold correct"*.

**1. `con-006`** — hybrid's top 3:
  1. `EU AI Act › Annex Iii — ANNEX III`
  2. `EU AI Act › Preamble › Recital (53)`
  3. `EU AI Act › Preamble › Recital (97) – Recital (98)`

**2. `con-009`** — hybrid's top 3:
  1. `EU AI Act › Preamble › Recital (1) – Recital (3)`
  2. `NVIDIA 10-K FY2026 › Item 1A. Risk Factors › Risks Related to Regulatory, Legal, Our Stock and Other Matters`
  3. `EU AI Act › Preamble › Recital (62) – Recital (64)`

**3. `con-010`** — hybrid's top 3:
  1. `EU AI Act › Preamble › Recital (72) – Recital (73)`
  2. `NIST AI RMF 1.0 › Appendix C: AI Risk Management and Human-AI Interaction`
  3. `EU AI Act › CHAPTER III › SECTION 2 › Article 14 — Human oversight`

**4. `con-011`** — hybrid's top 3:
  1. `EU AI Act › CHAPTER III › SECTION 2 › Article 9 — Risk management system`
  2. `EU AI Act › CHAPTER III › SECTION 3 › Article 24 — Obligations of distributors`
  3. `EU AI Act › CHAPTER III › SECTION 3 › Article 25 — Responsibilities along the AI value chain`

**5. `con-019`** — hybrid's top 3:
  1. `NVIDIA 10-K FY2026 › Item 1. Business › Information About Our Executive Officers`
  2. `NVIDIA 10-K FY2026 › Item 2. Properties – Item 5. Market for Registrant's Common Equity, Related Stockholder Matters and Issuer Purchases of Equity Securities`
  3. `NVIDIA 10-K FY2026 › Item 1. Business › Information About Our Executive Officers`

**6. `con-024`** — hybrid's top 3:
  1. `EU AI Act › Preamble › Recital (112) – Recital (114)`
  2. `EU AI Act › CHAPTER V › SECTION 1 › Article 51 — Classification of general-purpose AI models as general-purpose AI models with systemic risk – Article 52 — Procedure`
  3. `EU AI Act › Preamble › Recital (97) – Recital (98)`

**7. `con-042`** — hybrid's top 3:
  1. `EU AI Act › CHAPTER IX › SECTION 3 › Article 74 — Market surveillance and control of AI systems in the Union market`
  2. `EU AI Act › CHAPTER IX › SECTION 3 › Article 74 — Market surveillance and control of AI systems in the Union market`
  3. `EU AI Act › CHAPTER III › SECTION 3 › Article 25 — Responsibilities along the AI value chain`

**8. `con-051`** — hybrid's top 3:
  1. `EU AI Act › Annex Iv — ANNEX IV`
  2. `EU AI Act › CHAPTER III › SECTION 2 › Article 9 — Risk management system`
  3. `EU AI Act › Preamble › Recital (72) – Recital (73)`

**9. `con-063`** — hybrid's top 3:
  1. `EU AI Act › Annex Vii — ANNEX VII`
  2. `EU AI Act › CHAPTER III › SECTION 3 › Article 17 — Quality management system`
  3. `EU AI Act › Preamble › Recital (72) – Recital (73)`

**10. `con-064`** — hybrid's top 3:
  1. `EU AI Act › Preamble › Recital (106) – Recital (109)`
  2. `EU AI Act › Preamble › Recital (97) – Recital (98)`
  3. `EU AI Act › CHAPTER V › SECTION 2 › Article 53 — Obligations for providers of general-purpose AI models`

**11. `con-067`** — hybrid's top 3:
  1. `EU AI Act › CHAPTER III › SECTION 2 › Article 9 — Risk management system`
  2. `EU AI Act › CHAPTER III › SECTION 3 › Article 25 — Responsibilities along the AI value chain`
  3. `EU AI Act › Preamble › Recital (65) – Recital (66)`

**12. `con-070`** — hybrid's top 3:
  1. `NVIDIA 10-K FY2026 › Item 1A. Risk Factors › Climate change may have a long-term impact on our business.`
  2. `NVIDIA 10-K FY2026 › Item 1A. Risk Factors › Risks Related to Regulatory, Legal, Our Stock and Other Matters`
  3. `NVIDIA 10-K FY2026 › Item 1. Business › Government Regulations`

**13. `con-075`** — hybrid's top 3:
  1. `NVIDIA 10-K FY2026 › Item 1A. Risk Factors › Climate change may have a long-term impact on our business.`
  2. `NVIDIA 10-K FY2026 › Item 1A. Risk Factors › Climate change may have a long-term impact on our business.`
  3. `NVIDIA 10-K FY2026 › Item 1C. Cybersecurity › Risk management and strategy – Governance`

**14. `con-077`** — hybrid's top 3:
  1. `NVIDIA 10-K FY2026 › Item 1A. Risk Factors › Risks Related to Demand, Supply, and Manufacturing`
  2. `NVIDIA 10-K FY2026 › Item 15. Exhibits and Financial Statement Schedules › Product Sales Revenue – Stock-based Compensation`
  3. `NVIDIA 10-K FY2026 › Item 15. Exhibits and Financial Statement Schedules › Critical Audit Matters`

**15. `con-080`** — hybrid's top 3:
  1. `NIST AI RMF 1.0 › AI Risks and Trustworthiness`
  2. `NIST AI RMF 1.0 › Appendix B: How AI Risks Differ from Traditional Software Risks`
  3. `NIST AI RMF 1.0 › Executive Summary`

**16. `con-093`** — hybrid's top 3:
  1. `EU AI Act › Preamble › Recital (72) – Recital (73)`
  2. `EU AI Act › CHAPTER III › SECTION 3 › Article 18 — Documentation keeping – Article 20 — Corrective actions and duty of information`
  3. `EU AI Act › Preamble › Recital (69) – Recital (71)`

**17. `con-096`** — hybrid's top 3:
  1. `EU AI Act › CHAPTER VI › Article 60 — Testing of high-risk AI systems in real world conditions outside AI regulatory sandboxes`
  2. `EU AI Act › CHAPTER VI › Article 60 — Testing of high-risk AI systems in real world conditions outside AI regulatory sandboxes`
  3. `EU AI Act › CHAPTER IX › SECTION 3 › Article 75 — Mutual assistance, market surveillance and control of general-purpose AI systems – Article 76 — Supervision of testing in real world conditions by market surveillance authorities`

**18. `con-100`** — hybrid's top 3:
  1. `EU AI Act › CHAPTER IX › SECTION 3 › Article 79 — Procedure at national level for dealing with AI systems presenting a risk`
  2. `EU AI Act › CHAPTER III › SECTION 5 › Article 43 — Conformity assessment`
  3. `EU AI Act › Preamble › Recital (128) – Recital (130)`

**19. `con-101`** — hybrid's top 3:
  1. `EU AI Act › Preamble › Recital (142) – Recital (143)`
  2. `EU AI Act › Preamble › Recital (106) – Recital (109)`
  3. `EU AI Act › CHAPTER III › SECTION 5 › Article 41 — Common specifications`

**20. `con-120`** — hybrid's top 3:
  1. `EU AI Act › Preamble › Recital (72) – Recital (73)`
  2. `NIST AI RMF 1.0 › AI Risks and Trustworthiness`
  3. `EU AI Act › Annex Iv — ANNEX IV`

