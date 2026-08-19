# SPDX-License-Identifier: MIT
"""The trade documents this app ships knowing about, and who asks for them.

SEEDED AS ROWS, WHICH IS THE POINT. Everything in this file could have been a
dictionary in `shipments.py` that the checklist builder consulted. It is written
to the database instead so that the moment an operator's broker disagrees with
it — and a broker will — the disagreement is settled by editing a record rather
than by waiting for a release. This module is the STARTING POSITION, not the
rules.

A SHIPPED TEMPLATE IS NEVER OVERWRITTEN once it exists. Same contract as the
wizard definitions, same reason: a template somebody tuned for their own trade
being reset by the next `bench migrate` is what would make "config not code" a
lie.

────────────────────────────────────────────────────────────────────────────
THE FIELD NAMES FOLLOW THE STANDARDS, AND THAT IS ALL THEY DO
────────────────────────────────────────────────────────────────────────────

`required_fields` on the export templates is named from the published data
models — IPPC/ISPM-12 for the phytosanitary certificate, the DCSA data model for
the electronic bill of lading, 15 CFR 30's EEI elements for the AES declaration,
the WCO's origin criteria for the certificate of origin. That is so a broker's
schema and this app's can be reconciled BY READING, rather than by somebody
guessing whether `consignee` here means the same as `consignee` there.

IT IS NOT AN IMPLEMENTATION OF THOSE STANDARDS. This app does not speak
UN/EDIFACT, does not lodge anything in PCIT, does not file an EEI in AES and
does not issue an eBL on a DCSA platform. It records that somebody did, and
which reference came back. Any template whose document is finished somewhere
else carries `requires_external_filing`, and a document of that type with no
reference is reported outstanding however approved it looks — which is the one
guard that stops this module from quietly implying a filing that never happened.
"""

#: Every template this app seeds. `tiers` is the scope; a template scoped to a
#: tier is offered on that tier's shipments and refused on the others unless a
#: caller says otherwise.
SHIPPED_TEMPLATES = (
	{
		"template_name": "Scale Ticket Reference",
		"document_type": "Scale Ticket Reference",
		"label_en": "Scale Ticket Reference",
		"label_es": "Referencia de Boleta de Báscula",
		"description_en": (
			"The weight this load was received at, as a reference to the ticket itself rather "
			"than a copy of it. The ticket is the record; this is the assertion that this "
			"shipment is that ticket's fruit."
		),
		"description_es": (
			"El peso con el que se recibió esta carga, como referencia a la boleta misma. La "
			"boleta es el registro; esto afirma que este envío es la fruta de esa boleta."
		),
		"tiers": "Local, Domestic, International",
		"sequence": 10,
		"auto_populate_from": "Scale Ticket",
		"auto_populate_map": {
			"ticket_number": "ticket_number",
			"net_weight": "net_weight",
			"weight_uom": "weight_uom",
			"variety": "variety",
			"grade": "grade",
			"weighed_on": "date",
		},
		"required_fields": [
			{
				"fieldname": "ticket_number",
				"label_en": "Ticket Number",
				"label_es": "Número de Boleta",
				"type": "text",
				"required": True,
			},
			{
				"fieldname": "net_weight",
				"label_en": "Net Weight",
				"label_es": "Peso Neto",
				"type": "number",
				"required": True,
			},
			{
				"fieldname": "weight_uom",
				"label_en": "Weight UOM",
				"label_es": "Unidad de Peso",
				"type": "text",
				"required": True,
			},
			{"fieldname": "variety", "label_en": "Variety", "label_es": "Variedad", "type": "text"},
			{"fieldname": "grade", "label_en": "Grade", "label_es": "Grado", "type": "text"},
			{
				"fieldname": "weighed_on",
				"label_en": "Weighed On",
				"label_es": "Fecha de Pesaje",
				"type": "date",
			},
		],
		"notes": "Points at an existing Scale Ticket. get_scale_ticket has the ticket itself.",
	},
	{
		"template_name": "Delivery Receipt",
		"document_type": "Delivery Receipt",
		"label_en": "Delivery Receipt",
		"label_es": "Comprobante de Entrega",
		"description_en": "Signed at the far end. What proves the fruit arrived and who took it.",
		"description_es": "Firmado en destino. Prueba que la fruta llegó y quién la recibió.",
		"tiers": "Local, Domestic",
		"sequence": 20,
		"requires_signature": True,
		"signature_role": "Employer Representative",
		"required_fields": [
			{
				"fieldname": "delivered_to",
				"label_en": "Delivered To",
				"label_es": "Entregado A",
				"type": "text",
				"required": True,
			},
			{
				"fieldname": "delivered_on",
				"label_en": "Delivered On",
				"label_es": "Fecha de Entrega",
				"type": "date",
				"required": True,
			},
			{
				"fieldname": "received_by",
				"label_en": "Received By",
				"label_es": "Recibido Por",
				"type": "text",
				"required": True,
			},
			{"fieldname": "packages", "label_en": "Packages", "label_es": "Bultos", "type": "number"},
			{
				"fieldname": "condition_on_arrival",
				"label_en": "Condition on Arrival",
				"label_es": "Condición al Llegar",
				"type": "long_text",
				"help": "Any damage, temperature excursion or short count, in the receiver's words.",
			},
		],
	},
	{
		"template_name": "Commercial Invoice",
		"document_type": "Commercial Invoice",
		"label_en": "Commercial Invoice",
		"label_es": "Factura Comercial",
		"description_en": (
			"What is being sold and for how much. On an export this is the document a customs "
			"value is taken from, which is why it draws from the Sales Invoice rather than "
			"restating it."
		),
		"description_es": (
			"Qué se vende y por cuánto. En una exportación es el documento del que se toma el "
			"valor en aduana."
		),
		"tiers": "Local, Domestic, International",
		"sequence": 30,
		"auto_populate_from": "Sales Invoice",
		"auto_populate_map": {
			"invoice_number": "name",
			"invoice_date": "posting_date",
			"currency": "currency",
			"total_value": "grand_total",
			"buyer": "customer_name",
		},
		"required_fields": [
			{
				"fieldname": "invoice_number",
				"label_en": "Invoice Number",
				"label_es": "Número de Factura",
				"type": "text",
				"required": True,
			},
			{
				"fieldname": "invoice_date",
				"label_en": "Invoice Date",
				"label_es": "Fecha de Factura",
				"type": "date",
				"required": True,
			},
			{
				"fieldname": "seller",
				"label_en": "Seller",
				"label_es": "Vendedor",
				"type": "text",
				"required": True,
			},
			{
				"fieldname": "buyer",
				"label_en": "Buyer",
				"label_es": "Comprador",
				"type": "text",
				"required": True,
			},
			{
				"fieldname": "currency",
				"label_en": "Currency",
				"label_es": "Moneda",
				"type": "text",
				"required": True,
			},
			{
				"fieldname": "total_value",
				"label_en": "Total Value",
				"label_es": "Valor Total",
				"type": "number",
				"required": True,
			},
			{
				"fieldname": "incoterms",
				"label_en": "Incoterms",
				"label_es": "Incoterms",
				"type": "text",
				"help": "FOB, CIF, DDP — who bears cost and risk to where. A customs value is read differently under each.",
			},
			{
				"fieldname": "country_of_origin",
				"label_en": "Country of Origin",
				"label_es": "País de Origen",
				"type": "text",
			},
			{
				"fieldname": "harmonized_code",
				"label_en": "Harmonized Code",
				"label_es": "Código Arancelario",
				"type": "text",
				"help": "HS heading for the commodity. 0809.29 is sweet cherries, fresh.",
			},
		],
	},
	{
		"template_name": "Grade Certificate",
		"document_type": "Grade Certificate",
		"label_en": "Grade Certificate",
		"label_es": "Certificado de Grado",
		"description_en": "What the fruit graded, who graded it and against which standard.",
		"description_es": "Qué grado obtuvo la fruta, quién la clasificó y bajo qué norma.",
		"tiers": "Local, Domestic, International",
		"sequence": 40,
		"required_fields": [
			{
				"fieldname": "grade",
				"label_en": "Grade",
				"label_es": "Grado",
				"type": "text",
				"required": True,
			},
			{
				"fieldname": "grading_standard",
				"label_en": "Grading Standard",
				"label_es": "Norma de Clasificación",
				"type": "text",
				"required": True,
			},
			{"fieldname": "size", "label_en": "Size", "label_es": "Calibre", "type": "text"},
			{
				"fieldname": "graded_by",
				"label_en": "Graded By",
				"label_es": "Clasificado Por",
				"type": "text",
			},
			{
				"fieldname": "graded_on",
				"label_en": "Graded On",
				"label_es": "Fecha de Clasificación",
				"type": "date",
			},
			{
				"fieldname": "defects",
				"label_en": "Defects Noted",
				"label_es": "Defectos Anotados",
				"type": "long_text",
			},
		],
	},
	{
		"template_name": "USDA Grade Certificate",
		"document_type": "USDA Grade Certificate",
		"label_en": "USDA Grade Certificate",
		"label_es": "Certificado de Grado USDA",
		"description_en": (
			"Issued by a licensed federal or federal-state inspector, not by the shipper. A "
			"buyer who specified US No. 1 is buying this piece of paper as much as the fruit."
		),
		"description_es": (
			"Emitido por un inspector federal autorizado, no por el expedidor. Un comprador que "
			"pidió US No. 1 está comprando este documento tanto como la fruta."
		),
		"tiers": "Domestic, International",
		"sequence": 45,
		"standard_reference": "USDA AMS 7 CFR 51",
		"requires_signature": True,
		"signature_role": "Inspector",
		"required_fields": [
			{
				"fieldname": "certificate_number",
				"label_en": "Certificate Number",
				"label_es": "Número de Certificado",
				"type": "text",
				"required": True,
			},
			{
				"fieldname": "grade",
				"label_en": "Grade",
				"label_es": "Grado",
				"type": "text",
				"required": True,
			},
			{
				"fieldname": "inspection_point",
				"label_en": "Inspection Point",
				"label_es": "Punto de Inspección",
				"type": "text",
				"required": True,
			},
			{
				"fieldname": "inspector_name",
				"label_en": "Inspector",
				"label_es": "Inspector",
				"type": "text",
				"required": True,
			},
			{
				"fieldname": "inspected_on",
				"label_en": "Inspected On",
				"label_es": "Fecha de Inspección",
				"type": "date",
				"required": True,
			},
			{
				"fieldname": "lot_identification",
				"label_en": "Lot Identification",
				"label_es": "Identificación del Lote",
				"type": "text",
			},
		],
		"notes": "Booked with a federal-state inspection office. Not something the desk can issue itself.",
	},
	{
		"template_name": "Packing List",
		"document_type": "Packing List",
		"label_en": "Packing List",
		"label_es": "Lista de Empaque",
		"description_en": (
			"What is physically in the load, pallet by pallet. The document a customs officer "
			"opens the container against."
		),
		"description_es": (
			"Lo que va físicamente en la carga, tarima por tarima. El documento contra el que un "
			"oficial de aduana abre el contenedor."
		),
		"tiers": "Domestic, International",
		"sequence": 35,
		"required_fields": [
			{
				"fieldname": "total_packages",
				"label_en": "Total Packages",
				"label_es": "Total de Bultos",
				"type": "number",
				"required": True,
			},
			{
				"fieldname": "package_type",
				"label_en": "Package Type",
				"label_es": "Tipo de Empaque",
				"type": "text",
				"required": True,
			},
			{
				"fieldname": "gross_weight",
				"label_en": "Gross Weight",
				"label_es": "Peso Bruto",
				"type": "number",
				"required": True,
			},
			{
				"fieldname": "net_weight",
				"label_en": "Net Weight",
				"label_es": "Peso Neto",
				"type": "number",
				"required": True,
			},
			{
				"fieldname": "pallet_count",
				"label_en": "Pallet Count",
				"label_es": "Número de Tarimas",
				"type": "number",
			},
			{
				"fieldname": "marks_and_numbers",
				"label_en": "Marks and Numbers",
				"label_es": "Marcas y Números",
				"type": "long_text",
			},
			{
				"fieldname": "lot_codes",
				"label_en": "Lot Codes",
				"label_es": "Códigos de Lote",
				"type": "long_text",
				"help": "What a recall is traced by. The single most useful line on this document.",
			},
		],
	},
	{
		"template_name": "Bill of Lading",
		"document_type": "Bill of Lading",
		"label_en": "Bill of Lading",
		"label_es": "Conocimiento de Embarque",
		"description_en": (
			"The carrier's receipt and the contract of carriage. Named from the DCSA data model "
			"so a paper bill and an eBL describe the same shipment in the same words."
		),
		"description_es": (
			"El recibo del transportista y el contrato de transporte. Nombrado según el modelo de datos DCSA."
		),
		"tiers": "Domestic, International",
		"sequence": 50,
		"standard_reference": "DCSA data model",
		"required_fields": [
			{
				"fieldname": "transport_document_reference",
				"label_en": "Transport Document Reference",
				"label_es": "Referencia del Documento de Transporte",
				"type": "text",
				"required": True,
			},
			{
				"fieldname": "shipper",
				"label_en": "Shipper",
				"label_es": "Expedidor",
				"type": "text",
				"required": True,
			},
			{
				"fieldname": "consignee",
				"label_en": "Consignee",
				"label_es": "Consignatario",
				"type": "text",
				"required": True,
			},
			{
				"fieldname": "notify_party",
				"label_en": "Notify Party",
				"label_es": "Parte a Notificar",
				"type": "text",
			},
			{
				"fieldname": "carrier",
				"label_en": "Carrier",
				"label_es": "Transportista",
				"type": "text",
				"required": True,
			},
			{
				"fieldname": "place_of_receipt",
				"label_en": "Place of Receipt",
				"label_es": "Lugar de Recepción",
				"type": "text",
			},
			{
				"fieldname": "port_of_loading",
				"label_en": "Port of Loading",
				"label_es": "Puerto de Carga",
				"type": "text",
			},
			{
				"fieldname": "port_of_discharge",
				"label_en": "Port of Discharge",
				"label_es": "Puerto de Descarga",
				"type": "text",
			},
			{"fieldname": "vessel", "label_en": "Vessel", "label_es": "Buque", "type": "text"},
			{
				"fieldname": "voyage_number",
				"label_en": "Voyage Number",
				"label_es": "Número de Viaje",
				"type": "text",
			},
			{
				"fieldname": "container_numbers",
				"label_en": "Container Numbers",
				"label_es": "Números de Contenedor",
				"type": "long_text",
			},
			{
				"fieldname": "freight_terms",
				"label_en": "Freight Terms",
				"label_es": "Términos de Flete",
				"type": "text",
			},
		],
	},
	{
		"template_name": "FSMA Food Safety Record",
		"document_type": "FSMA Food Safety Record",
		"label_en": "FSMA Food Safety Record",
		"label_es": "Registro de Inocuidad FSMA",
		"description_en": (
			"The traceability the Food Safety Modernization Act asks for at a shipping event — "
			"the lot codes, the growing location, the harvest date and who to call. A buyer's "
			"mock recall is TIMED, and an operation that cannot answer in four hours fails it."
		),
		"description_es": (
			"La trazabilidad que exige FSMA en un evento de envío. Un simulacro de retiro del "
			"comprador es cronometrado, y quien no responde en cuatro horas lo reprueba."
		),
		"tiers": "Domestic, International",
		"sequence": 60,
		"standard_reference": "FSMA 204 Subpart S — shipping event",
		"required_fields": [
			{
				"fieldname": "traceability_lot_code",
				"label_en": "Traceability Lot Code",
				"label_es": "Código de Lote de Trazabilidad",
				"type": "text",
				"required": True,
			},
			{
				"fieldname": "growing_location",
				"label_en": "Growing Location",
				"label_es": "Lugar de Cultivo",
				"type": "text",
				"required": True,
			},
			{
				"fieldname": "harvest_date",
				"label_en": "Harvest Date",
				"label_es": "Fecha de Cosecha",
				"type": "date",
				"required": True,
			},
			{
				"fieldname": "packing_location",
				"label_en": "Packing Location",
				"label_es": "Lugar de Empaque",
				"type": "text",
				"required": True,
			},
			{
				"fieldname": "shipped_from",
				"label_en": "Shipped From",
				"label_es": "Enviado Desde",
				"type": "text",
				"required": True,
			},
			{
				"fieldname": "shipped_to",
				"label_en": "Shipped To",
				"label_es": "Enviado A",
				"type": "text",
				"required": True,
			},
			{
				"fieldname": "traceability_contact",
				"label_en": "Traceability Contact",
				"label_es": "Contacto de Trazabilidad",
				"type": "text",
				"required": True,
				"help": "A name and a phone number somebody answers. A record naming a department is a record that fails a timed exercise.",
			},
		],
	},
	{
		"template_name": "Cold Chain Record",
		"document_type": "Cold Chain Record",
		"label_en": "Cold Chain Record",
		"label_es": "Registro de Cadena de Frío",
		"description_en": (
			"What temperature the load was held at and whether it ever left the range. An "
			"excursion recorded honestly is a claim somebody can settle; one omitted is a claim "
			"nobody can defend."
		),
		"description_es": (
			"A qué temperatura se mantuvo la carga y si alguna vez salió del rango. Una "
			"excursión registrada con honestidad es un reclamo que se puede resolver."
		),
		"tiers": "Domestic, International",
		"sequence": 65,
		"required_fields": [
			{
				"fieldname": "set_point_c",
				"label_en": "Set Point (°C)",
				"label_es": "Punto de Ajuste (°C)",
				"type": "number",
				"required": True,
			},
			{
				"fieldname": "pulp_temperature_at_loading_c",
				"label_en": "Pulp Temperature at Loading (°C)",
				"label_es": "Temperatura de Pulpa al Cargar (°C)",
				"type": "number",
				"required": True,
			},
			{
				"fieldname": "loaded_at",
				"label_en": "Loaded At",
				"label_es": "Hora de Carga",
				"type": "datetime",
				"required": True,
			},
			{
				"fieldname": "recorder_id",
				"label_en": "Recorder ID",
				"label_es": "ID del Registrador",
				"type": "text",
				"help": "The datalogger travelling with the load.",
			},
			{
				"fieldname": "excursions",
				"label_en": "Excursions",
				"label_es": "Excursiones",
				"type": "long_text",
				"help": "Every time the load left its range, with when and for how long. 'None' is an answer; blank is not.",
			},
			{
				"fieldname": "precooling_method",
				"label_en": "Precooling Method",
				"label_es": "Método de Preenfriado",
				"type": "text",
			},
		],
	},
	{
		"template_name": "Phytosanitary Certificate (ePhyto)",
		"document_type": "Phytosanitary Certificate (ePhyto)",
		"label_en": "Phytosanitary Certificate (ePhyto)",
		"label_es": "Certificado Fitosanitario (ePhyto)",
		"description_en": (
			"The importing country's plant-health authority is the audience. ISSUED BY A "
			"NATIONAL PLANT PROTECTION ORGANIZATION AND LODGED IN PCIT — this app records the "
			"certificate number that comes back and nothing more. A shipment sailing without "
			"one is held at the far end, not turned back at this one."
		),
		"description_es": (
			"La autoridad fitosanitaria del país importador es el destinatario. Lo emite una "
			"ONPF y se registra en PCIT; esta aplicación solo guarda el número que regresa."
		),
		"tiers": "International",
		"sequence": 70,
		"standard_reference": "IPPC ISPM-12",
		"requires_external_filing": True,
		"external_system": "PCIT",
		"requires_signature": True,
		"signature_role": "Inspector",
		"required_fields": [
			{
				"fieldname": "certificate_number",
				"label_en": "Certificate Number",
				"label_es": "Número de Certificado",
				"type": "text",
				"required": True,
			},
			{
				"fieldname": "exporting_country",
				"label_en": "Exporting Country",
				"label_es": "País Exportador",
				"type": "text",
				"required": True,
			},
			{
				"fieldname": "importing_country",
				"label_en": "Importing Country",
				"label_es": "País Importador",
				"type": "text",
				"required": True,
			},
			{
				"fieldname": "botanical_name",
				"label_en": "Botanical Name",
				"label_es": "Nombre Botánico",
				"type": "text",
				"required": True,
				"help": "Prunus avium for sweet cherry. The common name is not what a plant-health authority reads.",
			},
			{
				"fieldname": "place_of_origin",
				"label_en": "Place of Origin",
				"label_es": "Lugar de Origen",
				"type": "text",
				"required": True,
			},
			{
				"fieldname": "declared_quantity",
				"label_en": "Declared Quantity",
				"label_es": "Cantidad Declarada",
				"type": "text",
				"required": True,
			},
			{
				"fieldname": "distinguishing_marks",
				"label_en": "Distinguishing Marks",
				"label_es": "Marcas Distintivas",
				"type": "text",
			},
			{
				"fieldname": "additional_declaration",
				"label_en": "Additional Declaration",
				"label_es": "Declaración Adicional",
				"type": "long_text",
				"help": "The wording the importing country demands verbatim. A paraphrase is a rejected certificate.",
			},
			{"fieldname": "treatment", "label_en": "Treatment", "label_es": "Tratamiento", "type": "text"},
			{
				"fieldname": "treatment_chemical",
				"label_en": "Treatment Chemical",
				"label_es": "Producto del Tratamiento",
				"type": "text",
			},
			{
				"fieldname": "treatment_duration_and_temperature",
				"label_en": "Duration and Temperature",
				"label_es": "Duración y Temperatura",
				"type": "text",
			},
			{
				"fieldname": "treatment_date",
				"label_en": "Treatment Date",
				"label_es": "Fecha del Tratamiento",
				"type": "date",
			},
			{
				"fieldname": "pest_declarations",
				"label_en": "Pest Declarations",
				"label_es": "Declaraciones de Plagas",
				"type": "long_text",
			},
			{
				"fieldname": "inspection_date",
				"label_en": "Inspection Date",
				"label_es": "Fecha de Inspección",
				"type": "date",
			},
		],
		"notes": (
			"Lodged in PCIT. An inspection has to be booked first, so allow several working "
			"days — this is the document that most often decides whether a container makes its "
			"sailing."
		),
	},
	{
		"template_name": "Certificate of Origin",
		"document_type": "Certificate of Origin",
		"label_en": "Certificate of Origin",
		"label_es": "Certificado de Origen",
		"description_en": (
			"Where the goods were produced, and under which rule that is claimed. What a "
			"preferential tariff rate is granted against."
		),
		"description_es": (
			"Dónde se produjeron las mercancías y bajo qué regla se reclama. Contra esto se "
			"concede una tarifa preferencial."
		),
		"tiers": "International",
		"sequence": 75,
		"standard_reference": "WCO origin criteria",
		"requires_signature": True,
		"signature_role": "Officer",
		"required_fields": [
			{
				"fieldname": "exporter",
				"label_en": "Exporter",
				"label_es": "Exportador",
				"type": "text",
				"required": True,
			},
			{
				"fieldname": "manufacturer",
				"label_en": "Producer or Manufacturer",
				"label_es": "Productor o Fabricante",
				"type": "text",
				"required": True,
			},
			{
				"fieldname": "consignee",
				"label_en": "Consignee",
				"label_es": "Consignatario",
				"type": "text",
				"required": True,
			},
			{
				"fieldname": "country_of_origin",
				"label_en": "Country of Origin",
				"label_es": "País de Origen",
				"type": "text",
				"required": True,
			},
			{
				"fieldname": "harmonized_code",
				"label_en": "Harmonized Code",
				"label_es": "Código Arancelario",
				"type": "text",
				"required": True,
			},
			{
				"fieldname": "origin_criterion",
				"label_en": "Origin Criterion",
				"label_es": "Criterio de Origen",
				"type": "text",
				"required": True,
				"help": "Wholly obtained, substantial transformation, or the agreement's own lettered criterion. Fruit grown and picked on the farm is wholly obtained.",
			},
			{
				"fieldname": "trade_agreement",
				"label_en": "Trade Agreement",
				"label_es": "Acuerdo Comercial",
				"type": "text",
			},
			{
				"fieldname": "issuing_body",
				"label_en": "Issuing Body",
				"label_es": "Organismo Emisor",
				"type": "text",
				"help": "A chamber of commerce, where the destination requires a chambered certificate rather than a self-declaration.",
			},
		],
	},
	{
		"template_name": "AES Export Declaration",
		"document_type": "AES Export Declaration",
		"label_en": "AES Export Declaration (EEI)",
		"label_es": "Declaración de Exportación AES (EEI)",
		"description_en": (
			"Electronic Export Information, filed in the Automated Export System. FILED BY THE "
			"EXPORTER OR THEIR AGENT — this app records the Internal Transaction Number that "
			"comes back, which is what the carrier needs before the container is laden."
		),
		"description_es": (
			"Información Electrónica de Exportación, presentada en AES. La presenta el "
			"exportador o su agente; esta aplicación registra el ITN que regresa."
		),
		"tiers": "International",
		"sequence": 80,
		"standard_reference": "AES/EEI 15 CFR 30",
		"requires_external_filing": True,
		"external_system": "AES",
		"required_fields": [
			{
				"fieldname": "itn",
				"label_en": "Internal Transaction Number (ITN)",
				"label_es": "Número de Transacción Interna (ITN)",
				"type": "text",
				"required": True,
				"help": "What AES returns on acceptance. The carrier will ask for it.",
			},
			{
				"fieldname": "usppi",
				"label_en": "US Principal Party in Interest",
				"label_es": "Parte Principal Estadounidense",
				"type": "text",
				"required": True,
			},
			{
				"fieldname": "usppi_ein",
				"label_en": "USPPI EIN",
				"label_es": "EIN de la USPPI",
				"type": "text",
				"required": True,
			},
			{
				"fieldname": "ultimate_consignee",
				"label_en": "Ultimate Consignee",
				"label_es": "Consignatario Final",
				"type": "text",
				"required": True,
			},
			{
				"fieldname": "consignee_type",
				"label_en": "Consignee Type",
				"label_es": "Tipo de Consignatario",
				"type": "text",
			},
			{
				"fieldname": "schedule_b_number",
				"label_en": "Schedule B Number",
				"label_es": "Número Schedule B",
				"type": "text",
				"required": True,
				"help": "The 10-digit US export commodity classification. 0809.29.0000 is fresh sweet cherries.",
			},
			{
				"fieldname": "eccn",
				"label_en": "ECCN",
				"label_es": "ECCN",
				"type": "text",
				"help": "EAR99 for most agricultural goods.",
			},
			{
				"fieldname": "license_code",
				"label_en": "License Code",
				"label_es": "Código de Licencia",
				"type": "text",
				"help": "C33 / NLR where no licence is required.",
			},
			{
				"fieldname": "port_of_export",
				"label_en": "Port of Export",
				"label_es": "Puerto de Exportación",
				"type": "text",
				"required": True,
			},
			{
				"fieldname": "country_of_ultimate_destination",
				"label_en": "Country of Ultimate Destination",
				"label_es": "País de Destino Final",
				"type": "text",
				"required": True,
			},
			{
				"fieldname": "value",
				"label_en": "Value",
				"label_es": "Valor",
				"type": "number",
				"required": True,
			},
			{
				"fieldname": "export_date",
				"label_en": "Export Date",
				"label_es": "Fecha de Exportación",
				"type": "date",
			},
			{
				"fieldname": "routed_transaction",
				"label_en": "Routed Transaction",
				"label_es": "Transacción Enrutada",
				"type": "checkbox",
				"help": "Whether the foreign buyer engaged the forwarder. It moves who is responsible for filing.",
			},
		],
		"notes": "Filed in AES, usually by a forwarder. The ITN is what proves it was accepted.",
	},
	{
		"template_name": "Fumigation Certificate",
		"document_type": "Fumigation Certificate",
		"label_en": "Fumigation Certificate",
		"label_es": "Certificado de Fumigación",
		"description_en": (
			"What the load was treated with, at what dose, for how long and at what "
			"temperature. Often the evidence behind the phytosanitary certificate's additional "
			"declaration."
		),
		"description_es": (
			"Con qué se trató la carga, a qué dosis, por cuánto tiempo y a qué temperatura. "
			"Suele ser la evidencia detrás de la declaración adicional del certificado "
			"fitosanitario."
		),
		"tiers": "International",
		"sequence": 85,
		"requires_signature": True,
		"signature_role": "Inspector",
		"required_fields": [
			{
				"fieldname": "certificate_number",
				"label_en": "Certificate Number",
				"label_es": "Número de Certificado",
				"type": "text",
				"required": True,
			},
			{
				"fieldname": "fumigant",
				"label_en": "Fumigant",
				"label_es": "Fumigante",
				"type": "text",
				"required": True,
			},
			{
				"fieldname": "dosage",
				"label_en": "Dosage",
				"label_es": "Dosis",
				"type": "text",
				"required": True,
			},
			{
				"fieldname": "duration_hours",
				"label_en": "Duration (hours)",
				"label_es": "Duración (horas)",
				"type": "number",
				"required": True,
			},
			{
				"fieldname": "temperature_c",
				"label_en": "Temperature (°C)",
				"label_es": "Temperatura (°C)",
				"type": "number",
				"required": True,
			},
			{
				"fieldname": "treatment_date",
				"label_en": "Treatment Date",
				"label_es": "Fecha del Tratamiento",
				"type": "date",
				"required": True,
			},
			{
				"fieldname": "treatment_location",
				"label_en": "Treatment Location",
				"label_es": "Lugar del Tratamiento",
				"type": "text",
			},
			{"fieldname": "applicator", "label_en": "Applicator", "label_es": "Aplicador", "type": "text"},
		],
	},
	{
		"template_name": "Electronic Bill of Lading (eBL)",
		"document_type": "Electronic Bill of Lading (eBL)",
		"label_en": "Electronic Bill of Lading (eBL)",
		"label_es": "Conocimiento de Embarque Electrónico (eBL)",
		"description_en": (
			"The bill of lading as a transferable electronic record. ISSUED ON A DCSA-CONFORMANT "
			"PLATFORM, not here — this app records the transport document reference and which "
			"platform holds it. Possession of an eBL is title to the goods, and this app holds "
			"no title."
		),
		"description_es": (
			"El conocimiento de embarque como registro electrónico transferible. Se emite en una "
			"plataforma conforme a DCSA, no aquí."
		),
		"tiers": "International",
		"sequence": 90,
		"standard_reference": "DCSA eBL data model",
		"requires_external_filing": True,
		"external_system": "DCSA platform",
		"required_fields": [
			{
				"fieldname": "transport_document_reference",
				"label_en": "Transport Document Reference",
				"label_es": "Referencia del Documento de Transporte",
				"type": "text",
				"required": True,
			},
			{
				"fieldname": "platform",
				"label_en": "eBL Platform",
				"label_es": "Plataforma eBL",
				"type": "text",
				"required": True,
			},
			{
				"fieldname": "shipper",
				"label_en": "Shipper",
				"label_es": "Expedidor",
				"type": "text",
				"required": True,
			},
			{
				"fieldname": "consignee",
				"label_en": "Consignee",
				"label_es": "Consignatario",
				"type": "text",
				"required": True,
			},
			{
				"fieldname": "notify_party",
				"label_en": "Notify Party",
				"label_es": "Parte a Notificar",
				"type": "text",
			},
			{
				"fieldname": "carrier",
				"label_en": "Carrier",
				"label_es": "Transportista",
				"type": "text",
				"required": True,
			},
			{"fieldname": "vessel", "label_en": "Vessel", "label_es": "Buque", "type": "text"},
			{
				"fieldname": "voyage_number",
				"label_en": "Voyage Number",
				"label_es": "Número de Viaje",
				"type": "text",
			},
			{
				"fieldname": "port_of_loading",
				"label_en": "Port of Loading",
				"label_es": "Puerto de Carga",
				"type": "text",
				"required": True,
			},
			{
				"fieldname": "port_of_discharge",
				"label_en": "Port of Discharge",
				"label_es": "Puerto de Descarga",
				"type": "text",
				"required": True,
			},
			{
				"fieldname": "container_numbers",
				"label_en": "Container Numbers",
				"label_es": "Números de Contenedor",
				"type": "long_text",
			},
			{
				"fieldname": "shipped_on_board_date",
				"label_en": "Shipped On Board Date",
				"label_es": "Fecha de Embarque",
				"type": "date",
			},
			{
				"fieldname": "is_to_order",
				"label_en": "To Order",
				"label_es": "A la Orden",
				"type": "checkbox",
				"help": "A to-order eBL is negotiable. Whoever holds it holds the goods.",
			},
		],
		"notes": "Issued on a DCSA-conformant platform. This app records the reference, never the title.",
	},
	{
		"template_name": "Import Permit Reference",
		"document_type": "Import Permit Reference",
		"label_en": "Import Permit Reference",
		"label_es": "Referencia de Permiso de Importación",
		"description_en": (
			"The permit the BUYER holds, referenced here because the shipment cannot clear "
			"without it and the seller is the one who finds out. Permits expire, and a shipment "
			"arriving after one lapses is a shipment sitting on a dock."
		),
		"description_es": (
			"El permiso que posee el COMPRADOR, referenciado aquí porque sin él el envío no "
			"despacha. Los permisos vencen."
		),
		"tiers": "International",
		"sequence": 95,
		"required_fields": [
			{
				"fieldname": "permit_number",
				"label_en": "Permit Number",
				"label_es": "Número de Permiso",
				"type": "text",
				"required": True,
			},
			{
				"fieldname": "issuing_authority",
				"label_en": "Issuing Authority",
				"label_es": "Autoridad Emisora",
				"type": "text",
				"required": True,
			},
			{
				"fieldname": "permit_holder",
				"label_en": "Permit Holder",
				"label_es": "Titular del Permiso",
				"type": "text",
				"required": True,
			},
			{"fieldname": "valid_from", "label_en": "Valid From", "label_es": "Válido Desde", "type": "date"},
			{
				"fieldname": "valid_to",
				"label_en": "Valid To",
				"label_es": "Válido Hasta",
				"type": "date",
				"help": "Set the document's own expires_on to this. An expired permit is reported as not satisfied, which is the point.",
			},
			{
				"fieldname": "permitted_commodity",
				"label_en": "Permitted Commodity",
				"label_es": "Producto Autorizado",
				"type": "text",
			},
			{
				"fieldname": "conditions",
				"label_en": "Conditions",
				"label_es": "Condiciones",
				"type": "long_text",
			},
		],
	},
	{
		"template_name": "Insurance Certificate",
		"document_type": "Insurance Certificate",
		"label_en": "Insurance Certificate",
		"label_es": "Certificado de Seguro",
		"description_en": "Marine cargo cover for the voyage. Required under a CIF or CIP sale.",
		"description_es": "Cobertura de carga marítima para el viaje. Requerido en una venta CIF o CIP.",
		"tiers": "International",
		"sequence": 100,
		"required_fields": [
			{
				"fieldname": "policy_number",
				"label_en": "Policy Number",
				"label_es": "Número de Póliza",
				"type": "text",
				"required": True,
			},
			{
				"fieldname": "insurer",
				"label_en": "Insurer",
				"label_es": "Aseguradora",
				"type": "text",
				"required": True,
			},
			{
				"fieldname": "insured_value",
				"label_en": "Insured Value",
				"label_es": "Valor Asegurado",
				"type": "number",
				"required": True,
			},
			{
				"fieldname": "coverage",
				"label_en": "Coverage",
				"label_es": "Cobertura",
				"type": "text",
				"help": "Institute Cargo Clauses A, B or C.",
			},
			{
				"fieldname": "claims_agent",
				"label_en": "Claims Agent at Destination",
				"label_es": "Agente de Reclamos en Destino",
				"type": "text",
			},
		],
	},
)


#: The destinations this app seeds rules for, and what each asks for. Only the
#: three TIERS — no country is named anywhere in this app, because the moment one
#: is, adding the next one is a release.
#:
#: `(tier, country, [(template, required, sequence, notes)])`
SHIPPED_REQUIREMENTS = (
	(
		"Local",
		"",
		(
			("Scale Ticket Reference", True, 10, "The weight the load was received at."),
			("Delivery Receipt", True, 20, "Signed at the far end. What proves it arrived."),
			("Commercial Invoice", True, 30, "What is being sold and for how much."),
			("Grade Certificate", True, 40, "What it graded and against which standard."),
		),
	),
	(
		"Domestic",
		"",
		(
			("Scale Ticket Reference", True, 10, "The weight the load was received at."),
			("Delivery Receipt", True, 20, "Signed at the far end."),
			("Commercial Invoice", True, 30, "What is being sold and for how much."),
			("Grade Certificate", True, 40, "The shipper's own grade."),
			(
				"USDA Grade Certificate",
				True,
				45,
				"Booked with a federal-state inspection office; the inspector issues it, not the "
				"desk. A buyer who specified US No. 1 is buying this document.",
			),
			("Packing List", True, 35, "What is physically in the load, pallet by pallet."),
			(
				"Bill of Lading",
				True,
				50,
				"The carrier's receipt and the contract of carriage.",
			),
			(
				"FSMA Food Safety Record",
				True,
				60,
				"The shipping-event traceability FSMA 204 asks for. A buyer's mock recall is "
				"timed — four hours is the number that gets quoted.",
			),
			(
				"Cold Chain Record",
				True,
				65,
				"Set point, pulp temperature at loading, and every excursion. 'None' is an "
				"answer; blank is not.",
			),
		),
	),
	(
		"International",
		"",
		(
			("Commercial Invoice", True, 30, "What a customs value is taken from."),
			("Packing List", True, 35, "What a customs officer opens the container against."),
			("Grade Certificate", True, 40, "What it graded."),
			(
				"Bill of Lading",
				True,
				50,
				"Or an eBL. Both are on the register; which one this trade uses is the "
				"operator's call, so both are seeded and either satisfies the desk.",
			),
			(
				"FSMA Food Safety Record",
				True,
				60,
				"US-origin exports carry the same shipping-event traceability.",
			),
			("Cold Chain Record", True, 65, "The voyage is the longest part of the cold chain."),
			(
				"Phytosanitary Certificate (ePhyto)",
				True,
				70,
				"LODGED IN PCIT by the national plant protection organization. An inspection has "
				"to be booked first — allow several working days. This is the document that most "
				"often decides whether a container makes its sailing.",
			),
			(
				"Certificate of Origin",
				True,
				75,
				"Where the goods were produced and under which rule that is claimed. Some "
				"destinations require it chambered rather than self-declared.",
			),
			(
				"AES Export Declaration",
				True,
				80,
				"Electronic Export Information filed in AES, usually by the forwarder. The ITN "
				"that comes back is what the carrier asks for before the container is laden.",
			),
			(
				"Electronic Bill of Lading (eBL)",
				False,
				90,
				"Issued on a DCSA-conformant platform. Not required by default because a paper "
				"bill still moves most fruit — turn it on with set_destination_requirements "
				"where the trade uses one.",
			),
			(
				"Fumigation Certificate",
				False,
				85,
				"Only where the destination's additional declaration calls for a treatment. Off "
				"by default because requiring a fumigation nobody asked for is how a desk learns "
				"to ignore the checklist.",
			),
			(
				"Import Permit Reference",
				False,
				95,
				"The permit the BUYER holds. Off by default because not every destination "
				"operates one — turn it on per country where it does, and set the document's "
				"expires_on to the permit's own expiry.",
			),
			(
				"Insurance Certificate",
				False,
				100,
				"Required under a CIF or CIP sale, and not otherwise. Off by default for the "
				"same reason as the fumigation certificate.",
			),
		),
	),
)
