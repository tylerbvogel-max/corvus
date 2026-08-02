import { useState, useEffect } from 'react';
import { fetchQueryRunCounts } from '../api/knowledge_graph';

interface SampleQuery {
  text: string;
  complexity: 'low' | 'medium' | 'high';
  suite: 'core' | 'x-plane' | 'proto-ready' | 'activation' | 'eval-corpus';
  roles: string[];
}

const SAMPLES: SampleQuery[] = [
  // ═══════════════════════════════════════════════════════════════════
  // CORE SUITE — existing queries
  // ═══════════════════════════════════════════════════════════════════

  // --- LOW COMPLEXITY (single domain, straightforward) ---
  {
    text: 'What are the key clauses in FAR Part 15 that govern competitive negotiation procedures?',
    complexity: 'low',
    suite: 'core',
    roles: ['Contracts Manager', 'FAR/DFARS Specialist'],
  },
  {
    text: 'What is the medallion architecture in Databricks and how does it structure ELT pipelines?',
    complexity: 'low',
    suite: 'core',
    roles: ['Data Engineer'],
  },
  {
    text: 'What are the AS9100 Rev D requirements for document control and records management?',
    complexity: 'low',
    suite: 'core',
    roles: ['Quality Manager', 'AS9100 Rev D'],
  },
  {
    text: 'How does earned value management (EVM) track cost and schedule performance on a contract?',
    complexity: 'low',
    suite: 'core',
    roles: ['Program Manager', 'Financial Analyst'],
  },
  {
    text: 'What personal protective equipment standards does OSHA 29 CFR 1910 require for manufacturing facilities?',
    complexity: 'low',
    suite: 'core',
    roles: ['Safety Officer', 'OSHA 29 CFR 1910'],
  },
  {
    text: 'What is the Shipley BD lifecycle and what are its major gate reviews?',
    complexity: 'low',
    suite: 'core',
    roles: ['BD Director', 'Capture Manager'],
  },
  {
    text: 'What are the NADCAP special process categories and how does accreditation work?',
    complexity: 'low',
    suite: 'core',
    roles: ['Quality Manager', 'NADCAP'],
  },
  {
    text: 'What cost pools and allocation bases does CAS 418 require for independent R&D and B&P costs?',
    complexity: 'low',
    suite: 'core',
    roles: ['Cost Accountant', 'Cost Estimator'],
  },

  // --- MEDIUM COMPLEXITY (cross-role, requires synthesis) ---
  {
    text: 'How should a program manager coordinate with contracts and finance when a customer requests an equitable adjustment on a cost-plus contract?',
    complexity: 'medium',
    suite: 'core',
    roles: ['Program Manager', 'Contracts Manager', 'Financial Analyst'],
  },
  {
    text: 'What is the interaction between ITAR export controls and the NADCAP audit process when a foreign national auditor needs facility access?',
    complexity: 'medium',
    suite: 'core',
    roles: ['Export Control Officer', 'Quality Manager', 'ITAR/EAR Export Controls', 'NADCAP'],
  },
  {
    text: 'How do manufacturing engineering and quality management jointly develop a process control plan that satisfies both AS9100 and customer-specific requirements?',
    complexity: 'medium',
    suite: 'core',
    roles: ['Manufacturing Engineer', 'Quality Manager', 'AS9100 Rev D'],
  },
  {
    text: 'What steps should the capture manager and proposal manager take to transition a qualified opportunity into a compliant proposal response?',
    complexity: 'medium',
    suite: 'core',
    roles: ['Capture Manager', 'Proposal Manager', 'BD Director'],
  },
  {
    text: 'How does the CFO work with program management to establish indirect rate structures that remain competitive while covering overhead?',
    complexity: 'medium',
    suite: 'core',
    roles: ['Chief Financial Officer', 'Program Manager', 'Cost Estimator'],
  },
  {
    text: 'What are the implications of DFARS 252.204-7012 (NIST SP 800-171) for the IT infrastructure supporting a manufacturing execution system?',
    complexity: 'medium',
    suite: 'core',
    roles: ['IT Support Specialist', 'NIST/CMMC', 'Production Manager'],
  },
  {
    text: 'How should industrial engineering and facilities management coordinate when designing a new production cell layout for a defense contract?',
    complexity: 'medium',
    suite: 'core',
    roles: ['Industrial Engineer', 'Facilities Manager', 'Production Manager'],
  },
  {
    text: 'What data quality checks should a data engineer implement when ingesting EVM performance data from multiple program managers into a central lakehouse?',
    complexity: 'medium',
    suite: 'core',
    roles: ['Data Engineer', 'Program Control Analyst', 'Financial Analyst'],
  },
  {
    text: 'How do GD&T callouts per ASME Y14.5 flow from the mechanical engineer\'s design intent through manufacturing process planning to final inspection?',
    complexity: 'medium',
    suite: 'core',
    roles: ['Mechanical Engineer', 'Manufacturing Engineer', 'ASME Y14.5'],
  },
  {
    text: 'What should the contracts manager and export control officer verify before issuing a technical assistance agreement for an international teaming arrangement?',
    complexity: 'medium',
    suite: 'core',
    roles: ['Contracts Manager', 'Export Control Officer', 'ITAR/EAR Export Controls'],
  },

  // --- HIGH COMPLEXITY (multi-department, strategic, requires deep synthesis) ---
  {
    text: 'A prime contractor is flowing down CMMC Level 2 requirements to our company as a sub-tier supplier. What changes are needed across IT infrastructure, contracts, manufacturing data handling, and employee training to achieve compliance?',
    complexity: 'high',
    suite: 'core',
    roles: ['NIST/CMMC', 'IT Support Specialist', 'Contracts Manager', 'Production Manager', 'HR Generalist'],
  },
  {
    text: 'We won a new CPFF development contract requiring NADCAP-accredited special processes, ITAR-controlled technical data, and AS9100 compliance. Map out the cross-functional launch sequence from contract award through first article inspection.',
    complexity: 'high',
    suite: 'core',
    roles: ['Program Manager', 'Quality Manager', 'Export Control Officer', 'Manufacturing Engineer', 'Contracts Manager', 'NADCAP', 'AS9100 Rev D', 'ITAR/EAR Export Controls'],
  },
  {
    text: 'The CEO wants a strategic assessment: if we acquire a small composites shop with NADCAP accreditation, what are the integration risks across quality systems, export controls, cost accounting, and facility operations?',
    complexity: 'high',
    suite: 'core',
    roles: ['Chief Executive Officer', 'VP Strategy', 'Quality Manager', 'Export Control Officer', 'Cost Accountant', 'Facilities Manager', 'NADCAP'],
  },
  {
    text: 'How should the VP of Engineering, CTO, and VP of Operations evaluate whether to invest in an automated NDT inspection cell versus continuing manual NAS 410 qualified inspectors, considering throughput, compliance, and workforce impacts?',
    complexity: 'high',
    suite: 'core',
    roles: ['VP Engineering', 'Chief Technology Officer', 'VP Operations', 'Industrial Engineer', 'NAS 410', 'Test Engineer'],
  },
  {
    text: 'A government audit has flagged potential CAS 401/402 noncompliance in how we allocate engineering labor between direct and indirect cost pools. What is the remediation path involving finance, contracts, program management, and executive leadership?',
    complexity: 'high',
    suite: 'core',
    roles: ['Chief Financial Officer', 'Cost Accountant', 'Contracts Manager', 'FAR/DFARS Specialist', 'Program Manager', 'Chief Executive Officer'],
  },
  {
    text: 'Design a data pipeline architecture that ingests real-time production floor data (MES, quality inspection, material tracking) into a Databricks lakehouse and surfaces it as EVM-compatible cost/schedule metrics for program managers and financial analysts.',
    complexity: 'high',
    suite: 'core',
    roles: ['Data Engineer', 'Production Manager', 'Quality Manager', 'Program Control Analyst', 'Financial Analyst'],
  },
  {
    text: 'We are bidding on a classified program requiring DO-178C Level A software, MIL-STD-810 environmental testing, and ITAR-restricted data handling. What proposal volume structure and compliance matrix should the proposal team build, and what organizational capabilities need to be demonstrated?',
    complexity: 'high',
    suite: 'core',
    roles: ['Proposal Manager', 'Software Engineer', 'Test Engineer', 'Export Control Officer', 'DO-178C/DO-254/DO-160G', 'MIL-STD Series', 'ITAR/EAR Export Controls'],
  },
  {
    text: 'The supply chain manager reports a sole-source critical casting supplier is at risk of losing NADCAP accreditation. What is the cross-functional response plan across supply chain, quality, engineering, program management, and contracts to mitigate delivery risk on active programs?',
    complexity: 'high',
    suite: 'core',
    roles: ['Supply Chain Manager', 'Quality Manager', 'Manufacturing Engineer', 'Program Manager', 'Contracts Manager', 'NADCAP'],
  },

  // ═══════════════════════════════════════════════════════════════════
  // X-PLANE BACKTEST SUITE — derived from historical X-plane programs
  // Tests whether the neuron graph could support experimental aircraft
  // development from cradle to grave.
  // ═══════════════════════════════════════════════════════════════════

  // --- Acquisition & Contract Strategy ---
  {
    text: 'What contract type is most appropriate for a DARPA technology demonstration program where the core propulsion system is at TRL 3? What risk-sharing mechanisms should be built into the contract structure?',
    complexity: 'medium',
    suite: 'x-plane',
    roles: ['Contracts Manager', 'Program Manager', 'FAR/DFARS Specialist'],
  },
  {
    text: 'How does an Other Transaction Authority (OTA) agreement differ from a traditional FAR-based contract for an experimental aircraft program, and what are the implications for cost accounting, IP rights, and audit requirements?',
    complexity: 'medium',
    suite: 'x-plane',
    roles: ['Contracts Manager', 'FAR/DFARS Specialist', 'Cost Accountant'],
  },
  {
    text: 'A DARPA program has experienced manufacturing cost overruns that exceed the remaining budget. What co-investment models exist where the contractor shares cost risk with the government to keep the program alive, and what are the contractual mechanisms?',
    complexity: 'high',
    suite: 'x-plane',
    roles: ['Contracts Manager', 'Chief Financial Officer', 'Program Manager', 'FAR/DFARS Specialist'],
  },

  // --- Technology Readiness & Systems Engineering ---
  {
    text: 'How should a program manager assess whether a subsystem at TRL 4 is ready to integrate into a flight vehicle, and what gates should exist between TRL advancement and system-level integration decisions?',
    complexity: 'medium',
    suite: 'x-plane',
    roles: ['Program Manager', 'VP Engineering', 'Test Engineer'],
  },
  {
    text: 'When integrating off-the-shelf components from different legacy platforms into a novel airframe (e.g., landing gear from one aircraft, stabilizers from another), what systems engineering processes prevent late-stage integration failures?',
    complexity: 'high',
    suite: 'x-plane',
    roles: ['VP Engineering', 'Mechanical Engineer', 'Manufacturing Engineer', 'Quality Manager'],
  },
  {
    text: 'What is the Manufacturing Readiness Level (MRL) assessment process, and how does it interact with TRL gates when transitioning a prototype subsystem from lab demonstration to flight-ready hardware?',
    complexity: 'medium',
    suite: 'x-plane',
    roles: ['Manufacturing Engineer', 'VP Engineering', 'Program Manager'],
  },

  // --- Test Planning & Envelope Expansion ---
  {
    text: 'What is the standard phased approach to flight test envelope expansion for an experimental aircraft, and how do you structure the gate reviews between ground test, taxi, first flight, and performance envelope expansion?',
    complexity: 'medium',
    suite: 'x-plane',
    roles: ['Test Engineer', 'Program Manager', 'Safety Officer'],
  },
  {
    text: 'An unmanned experimental aircraft crashed on its first flight with a new wing configuration. What should the accident investigation process look like, and how do findings feed forward into resuming flight test with the backup airframe?',
    complexity: 'high',
    suite: 'x-plane',
    roles: ['Test Engineer', 'Safety Officer', 'Quality Manager', 'VP Engineering', 'Program Manager'],
  },
  {
    text: 'How do you structure a test readiness review (TRR) for the first powered flight of an experimental propulsion system that has only been validated in ground test facilities? What risk acceptance criteria should the review board evaluate?',
    complexity: 'high',
    suite: 'x-plane',
    roles: ['Test Engineer', 'Program Manager', 'Safety Officer', 'VP Engineering'],
  },

  // --- Thermal Management & Materials ---
  {
    text: 'What thermal protection system qualification tests are required for sustained flight above Mach 4, and how does the test campaign account for the difference between ground facility simulation and actual flight thermal loads?',
    complexity: 'high',
    suite: 'x-plane',
    roles: ['Test Engineer', 'Manufacturing Engineer', 'Mechanical Engineer', 'VP Engineering'],
  },
  {
    text: 'A novel composite material is being used for an experimental airframe with no production heritage. What qualification path should manufacturing engineering follow, and what AS9100/NADCAP considerations apply to a one-off prototype vehicle?',
    complexity: 'medium',
    suite: 'x-plane',
    roles: ['Manufacturing Engineer', 'Quality Manager', 'NADCAP', 'AS9100 Rev D'],
  },

  // --- Risk Management & Redundancy ---
  {
    text: 'For an experimental unmanned aircraft program, what is the cost-benefit analysis of building two airframes versus one? How does the risk calculus change if the vehicle uses an unproven flight control architecture?',
    complexity: 'medium',
    suite: 'x-plane',
    roles: ['Program Manager', 'Cost Estimator', 'VP Engineering', 'Safety Officer'],
  },
  {
    text: 'A late-stage propulsion system failure mode has been discovered that poses safety risks to ground personnel during test operations. What is the decision framework for continuing the program versus cancellation, and what roles are involved?',
    complexity: 'high',
    suite: 'x-plane',
    roles: ['Program Manager', 'Safety Officer', 'VP Engineering', 'Chief Executive Officer', 'Contracts Manager'],
  },

  // --- EMI & Subsystem Integration ---
  {
    text: 'How should electromagnetic interference (EMI) requirements be specified and flowed down to subsystem vendors from program inception, and what happens when EMI requirements are omitted from initial subcontracts?',
    complexity: 'medium',
    suite: 'x-plane',
    roles: ['VP Engineering', 'Contracts Manager', 'Manufacturing Engineer', 'Quality Manager'],
  },
  {
    text: 'An experimental aircraft uses electric motor controllers that generate electromagnetic interference affecting avionics and flight control sensors. Individual subsystems passed component-level EMI testing but fail at system integration. What is the remediation path?',
    complexity: 'high',
    suite: 'x-plane',
    roles: ['VP Engineering', 'Test Engineer', 'Manufacturing Engineer', 'Quality Manager', 'Program Manager'],
  },

  // --- Autonomous Systems & Flight Control ---
  {
    text: 'What are the unique flight control law development and verification challenges when an aircraft uses active flow control (pressurized air jets) instead of traditional mechanical control surfaces?',
    complexity: 'medium',
    suite: 'x-plane',
    roles: ['VP Engineering', 'Software Engineer', 'Test Engineer'],
  },
  {
    text: 'How does a triple-redundant autonomous navigation system handle fault detection and mode reversion during a carrier-based autonomous landing, and what are the verification and validation requirements?',
    complexity: 'high',
    suite: 'x-plane',
    roles: ['Software Engineer', 'VP Engineering', 'Test Engineer', 'Safety Officer'],
  },

  // --- Cost & Schedule Management ---
  {
    text: 'An experimental aircraft program has doubled in cost from initial estimates due to integration complexity and supply chain delays. What EVM indicators should have flagged this trajectory earlier, and what corrective actions can the PMO take?',
    complexity: 'high',
    suite: 'x-plane',
    roles: ['Program Manager', 'Financial Analyst', 'Cost Estimator', 'Chief Financial Officer'],
  },
  {
    text: 'How should a cost estimate be structured for a one-of-a-kind experimental vehicle when there is no production baseline or analogous program to reference? What estimating methodologies apply?',
    complexity: 'medium',
    suite: 'x-plane',
    roles: ['Cost Estimator', 'Program Manager', 'Financial Analyst'],
  },

  // --- Regulatory & Airworthiness ---
  {
    text: 'What is the process for obtaining an experimental airworthiness certificate for a novel unmanned aircraft, and how do MIL-STD-882 system safety requirements apply to experimental flight test programs?',
    complexity: 'medium',
    suite: 'x-plane',
    roles: ['Safety Officer', 'Test Engineer', 'Program Manager', 'MIL-STD Series'],
  },
  {
    text: 'An experimental supersonic aircraft program needs to fly over populated areas to collect community noise response data. What regulatory approvals, instrumentation requirements, and stakeholder coordination are needed before overflights can begin?',
    complexity: 'high',
    suite: 'x-plane',
    roles: ['Program Manager', 'Safety Officer', 'Test Engineer', 'VP Engineering'],
  },

  // --- Lessons Learned & Knowledge Transfer ---
  {
    text: 'A technology demonstration program has been canceled before first flight, but produced hundreds of technical papers and test datasets. How should the organization formally capture lessons learned and preserve knowledge value for future programs?',
    complexity: 'medium',
    suite: 'x-plane',
    roles: ['Program Manager', 'VP Engineering', 'Chief Technology Officer'],
  },
  {
    text: 'A successful flight test program demonstrated a novel propulsion technology across four flights with two failures and two successes. How should the test data and lessons learned be packaged to enable a follow-on weapons development program to build on the results?',
    complexity: 'high',
    suite: 'x-plane',
    roles: ['Program Manager', 'Test Engineer', 'VP Engineering', 'BD Director', 'Chief Technology Officer'],
  },

  // --- Supply Chain & Manufacturing for Prototypes ---
  {
    text: 'What unique supply chain challenges arise when manufacturing a one-off experimental aircraft versus a production program, and how should the procurement strategy differ for specialty components with no second source?',
    complexity: 'medium',
    suite: 'x-plane',
    roles: ['Supply Chain Manager', 'Manufacturing Engineer', 'Program Manager'],
  },

  // --- Cross-Cutting Program Scenarios ---
  {
    text: 'We are proposing on a DARPA program to build and fly an experimental unmanned aircraft demonstrating a novel control technology at TRL 3. The program requires phased design reviews, flight test at a government range, and delivery of flight data. Build the proposal technical volume outline covering systems engineering approach, test strategy, risk management, and schedule.',
    complexity: 'high',
    suite: 'x-plane',
    roles: ['Proposal Manager', 'VP Engineering', 'Test Engineer', 'Program Manager', 'Safety Officer', 'Contracts Manager'],
  },
  {
    text: 'An experimental aircraft program is transitioning from land-based flight test to operations from a Navy carrier. What cross-functional coordination is required across engineering (EMI hardening, corrosion protection, structural reinforcement for catapult/arrested landing), logistics (carrier deck handling, maintenance), safety, and program management?',
    complexity: 'high',
    suite: 'x-plane',
    roles: ['VP Engineering', 'Program Manager', 'Safety Officer', 'Manufacturing Engineer', 'Test Engineer', 'Quality Manager'],
  },

  // ═══════════════════════════════════════════════════════════════════
  // PROTOTYPE READINESS SUITE — organizational and cultural competencies
  // for executing experimental aircraft programs. Tests whether the
  // graph captures skunkworks-style practices beyond pure technical domains.
  // ═══════════════════════════════════════════════════════════════════

  // --- Rapid SE Under Uncertainty ---
  {
    text: 'How should a systems engineering process be adapted for a prototype aircraft program where requirements are expected to evolve during flight test, rather than being frozen at PDR? What MBSE practices support this iterative approach?',
    complexity: 'medium',
    suite: 'proto-ready',
    roles: ['VP Engineering', 'Systems Engineer', 'Program Manager'],
  },
  {
    text: 'What is the role of hardware-in-the-loop simulation and digital twin models during early development of an experimental aircraft, and how do they reduce risk compared to traditional analysis-only approaches?',
    complexity: 'medium',
    suite: 'proto-ready',
    roles: ['VP Engineering', 'Systems Engineer', 'Test Engineer', 'Software Engineer'],
  },

  // --- Small Elite Team Operations ---
  {
    text: 'What organizational structure enables a 50-200 person engineering team to design, build, and fly an experimental aircraft in under 3 years? How does this differ from a traditional production program IPT structure?',
    complexity: 'medium',
    suite: 'proto-ready',
    roles: ['VP Engineering', 'Chief Executive Officer', 'Chief Operating Officer', 'Program Manager'],
  },
  {
    text: 'How should an aerospace company staff a prototype aircraft program when individual engineers need to cover multiple technical domains (structures, thermal, integration)? What training, hiring, and team composition strategies apply?',
    complexity: 'medium',
    suite: 'proto-ready',
    roles: ['VP Engineering', 'HR Generalist', 'Chief Technology Officer', 'Program Manager'],
  },

  // --- Leadership That Protects Speed ---
  {
    text: 'What leadership practices distinguish successful skunkworks-style prototype programs from traditional defense programs? How does leadership shield engineering teams from bureaucratic friction while maintaining safety and compliance?',
    complexity: 'high',
    suite: 'proto-ready',
    roles: ['Chief Executive Officer', 'VP Engineering', 'Program Manager', 'Chief Operating Officer'],
  },
  {
    text: 'A prototype program manager is under pressure to add formal gate reviews, additional documentation, and oversight layers that mirror the organization\'s production program processes. What is the risk to schedule and innovation, and how should leadership arbitrate?',
    complexity: 'high',
    suite: 'proto-ready',
    roles: ['Program Manager', 'VP Engineering', 'Chief Executive Officer', 'Quality Manager'],
  },

  // --- Configuration Agility ---
  {
    text: 'How should configuration management be adapted for a one-off experimental aircraft where design changes happen daily during build? What is the right balance between traceability and speed, and how does this differ from AS9100 production CM?',
    complexity: 'medium',
    suite: 'proto-ready',
    roles: ['VP Engineering', 'Quality Manager', 'Manufacturing Engineer', 'AS9100 Rev D'],
  },
  {
    text: 'An experimental aircraft program needs to implement a design change in 48 hours that would take 6 weeks through the normal ECO/CCB process. What expedited engineering authority structure allows rapid changes while preserving safety-critical traceability?',
    complexity: 'high',
    suite: 'proto-ready',
    roles: ['VP Engineering', 'Program Manager', 'Quality Manager', 'Safety Officer', 'Manufacturing Engineer'],
  },

  // --- Learning Rate & Failure Tolerance ---
  {
    text: 'How should an aerospace organization measure and optimize its learning rate during a prototype development program? What metrics capture whether design-build-test cycles are generating actionable knowledge versus just consuming schedule?',
    complexity: 'high',
    suite: 'proto-ready',
    roles: ['VP Engineering', 'Chief Technology Officer', 'Program Manager', 'Chief Executive Officer'],
  },
  {
    text: 'An X-plane prototype program has experienced two flight test failures but generated critical data that advances the state of the art. How should the organization frame the program\'s value to stakeholders, and what knowledge capture processes preserve the learning even if the program is canceled?',
    complexity: 'high',
    suite: 'proto-ready',
    roles: ['Chief Executive Officer', 'Program Manager', 'VP Engineering', 'Chief Technology Officer', 'BD Director'],
  },

  // --- Advanced Prototype Manufacturing ---
  {
    text: 'What manufacturing capabilities differentiate a prototype aircraft shop from a production facility? How should tooling, fabrication, and assembly processes be structured when building one or two vehicles with no production baseline?',
    complexity: 'medium',
    suite: 'proto-ready',
    roles: ['Manufacturing Engineer', 'VP Operations', 'Production Manager', 'Quality Manager'],
  },
  {
    text: 'During build of an experimental airframe, structural testing reveals a joint design that needs rework. The hardware is partially assembled with limited disassembly options. What engineering and manufacturing processes enable rapid in-situ redesign and repair without restarting the build?',
    complexity: 'high',
    suite: 'proto-ready',
    roles: ['Mechanical Engineer', 'Manufacturing Engineer', 'VP Engineering', 'Quality Manager', 'Test Engineer'],
  },

  // --- Government-Industry Integration for Prototypes ---
  {
    text: 'How does a DARPA-sponsored prototype program differ from a traditional FAR-based development contract in terms of sponsor technical engagement, milestone structure, and reporting requirements? What organizational capabilities enable success in this environment?',
    complexity: 'medium',
    suite: 'proto-ready',
    roles: ['Program Manager', 'Contracts Manager', 'VP Engineering', 'BD Director'],
  },
  {
    text: 'A prototype aircraft program operates under both ITAR restrictions and a classified program environment, with a government sponsor who wants embedded engineers on the contractor floor. What security, export control, and facility arrangements are required, and how do they interact with the need for development speed?',
    complexity: 'high',
    suite: 'proto-ready',
    roles: ['Program Manager', 'Export Control Officer', 'Facilities Manager', 'Safety Officer', 'VP Engineering', 'ITAR/EAR Export Controls'],
  },

  // ═══════════════════════════════════════════════════════════════════
  // ACTIVATION SUITE — targeted queries to wake up unfired neurons
  // Designed to exercise BD, Executive Leadership, Finance, Capture,
  // Proposal, Safety, and Cost Estimating roles.
  // ═══════════════════════════════════════════════════════════════════
  {
    text: "We're evaluating whether to bid on a $150M IDIQ recompete where we're the incumbent. Walk me through the PWIN assessment methodology, how to structure the teaming agreement negotiations with our subcontractors, and what the gate review criteria should look like before committing capture resources.",
    complexity: 'high',
    suite: 'activation',
    roles: ['Capture Manager', 'BD Director', 'Proposal Manager', 'VP BD'],
  },
  {
    text: 'Our DCAA auditor flagged issues with our indirect rate structure. Explain how to properly set up fringe, overhead, G&A, and material handling cost pools, how to prepare a CAS Disclosure Statement amendment, and what the forward pricing rate proposal process looks like when our provisional billing rates are diverging from actuals.',
    complexity: 'high',
    suite: 'activation',
    roles: ['Cost Accountant', 'Cost Estimator', 'Chief Financial Officer', 'FAR/DFARS Specialist'],
  },
  {
    text: 'The CEO wants a strategic growth plan for the next 3 years. How should we apply the Three Horizons framework and BCG portfolio matrix to prioritize our product lines, what PESTEL factors should we be scanning for in the defense aerospace market, and how do we structure the executive governance council to oversee the balanced scorecard implementation?',
    complexity: 'high',
    suite: 'activation',
    roles: ['Chief Executive Officer', 'VP Strategy', 'Chief Operating Officer', 'VP BD'],
  },
  {
    text: 'Walk me through setting up an Estimate at Completion process for a cost-plus-incentive-fee contract. How does the CFO oversee contract profitability analysis, what should the program financial planning and budget formulation process look like, and how do we ensure our indirect rate development stays compliant with FAR 31.205 allowability requirements?',
    complexity: 'high',
    suite: 'activation',
    roles: ['Chief Financial Officer', 'Financial Analyst', 'Cost Accountant', 'Program Manager'],
  },
  {
    text: "We're writing a proposal for a human-rated spacecraft component. How should the system safety analysis per MIL-STD-882E integrate into the proposal's technical volume, what does the compliance matrix development process look like for a Section L/M evaluation, and how do we coordinate the mishap investigation and reporting procedures as part of our safety management plan?",
    complexity: 'high',
    suite: 'activation',
    roles: ['Safety Officer', 'Proposal Manager', 'MIL-STD Series', 'Quality Manager', 'Systems Engineer'],
  },

  // ═══════════════════════════════════════════════════════════════════
  // EVAL CORPUS — investor demo & ROI validation queries
  // 10 questions a real compliance analyst asks weekly.
  // Run through Corvus AND raw Claude/GPT to produce side-by-side
  // comparison for 1-pager proof points and live demo script.
  // ═══════════════════════════════════════════════════════════════════
  {
    text: 'What are the key differences between FAR 52.225-25 and DFARS 252.225-7012 regarding foreign source restrictions, and when does each apply?',
    complexity: 'medium',
    suite: 'eval-corpus',
    roles: ['Contracts Manager', 'FAR/DFARS Specialist'],
  },
  {
    text: 'Our subcontract requires DFARS 252.204-7012 compliance. What specific controls do we need to implement for CUI protection, and how does this relate to CMMC Level 2?',
    complexity: 'medium',
    suite: 'eval-corpus',
    roles: ['Contracts Manager', 'NIST/CMMC', 'IT Support Specialist'],
  },
  {
    text: 'We\'re preparing for an AS9100 Rev D surveillance audit. What are the most commonly cited nonconformities in clause 8.5 (Production and Service Provision) and how should we prepare?',
    complexity: 'medium',
    suite: 'eval-corpus',
    roles: ['Quality Manager', 'AS9100 Rev D'],
  },
  {
    text: 'A supplier is NADCAP-accredited for special processes but their accreditation expires in 60 days. What are our obligations under AS9100 clause 8.4 for continued use of this supplier?',
    complexity: 'medium',
    suite: 'eval-corpus',
    roles: ['Quality Manager', 'Supply Chain Manager', 'NADCAP', 'AS9100 Rev D'],
  },
  {
    text: 'We\'re designing a composite structural component for a military aircraft. Which MIL-STDs govern composite material qualification and what testing requirements apply?',
    complexity: 'medium',
    suite: 'eval-corpus',
    roles: ['Mechanical Engineer', 'Manufacturing Engineer', 'MIL-STD Series'],
  },
  {
    text: 'We received a customer-reported nonconformity on a delivered part. Walk me through the corrective action process under AS9100 clause 10.2 including required documentation and timelines.',
    complexity: 'medium',
    suite: 'eval-corpus',
    roles: ['Quality Manager', 'AS9100 Rev D', 'Program Manager'],
  },
  {
    text: 'Our program is transitioning from EMD to LRIP. What contract modifications and compliance documentation need to be updated during this transition under FAR Part 15?',
    complexity: 'high',
    suite: 'eval-corpus',
    roles: ['Program Manager', 'Contracts Manager', 'FAR/DFARS Specialist'],
  },
  {
    text: 'We\'re implementing a new heat treatment process for titanium alloys. What NADCAP and AMS requirements apply, and what documentation do we need before production use?',
    complexity: 'medium',
    suite: 'eval-corpus',
    roles: ['Manufacturing Engineer', 'Quality Manager', 'NADCAP'],
  },
  {
    text: 'DCAA is scheduling an incurred cost audit. What are the most common findings in labor charging and how should we prepare our timekeeping system documentation?',
    complexity: 'high',
    suite: 'eval-corpus',
    roles: ['Cost Accountant', 'Chief Financial Officer', 'FAR/DFARS Specialist'],
  },
  {
    text: 'We just received a new ITAR-controlled technical data package from our prime. What are our obligations for marking, storage, access control, and re-export under ITAR §120-130, and how does this interact with our AS9100 document control procedures?',
    complexity: 'high',
    suite: 'eval-corpus',
    roles: ['Export Control Officer', 'Quality Manager', 'ITAR/EAR Export Controls', 'AS9100 Rev D'],
  },
];

const COMPLEXITY_COLORS: Record<string, string> = {
  low: '#22c55e',
  medium: '#fb923c',
  high: '#ef4444',
};

const SUITE_CONFIG: Record<string, { label: string; color: string }> = {
  core: { label: 'Core', color: '#60a5fa' },
  'x-plane': { label: 'X-Plane Backtest', color: '#c084fc' },
  'proto-ready': { label: 'Prototype Readiness', color: '#34d399' },
  activation: { label: 'Neuron Activation', color: '#fb7185' },
  'eval-corpus': { label: 'Eval Corpus', color: '#facc15' },
};

export default function SampleQueries() {
  const [complexityFilter, setComplexityFilter] = useState<'all' | 'low' | 'medium' | 'high'>('all');
  const [suiteFilter, setSuiteFilter] = useState<'all' | 'core' | 'x-plane' | 'proto-ready' | 'activation' | 'eval-corpus'>('all');
  const [copied, setCopied] = useState<number | null>(null);
  const [runCounts, setRunCounts] = useState<Record<string, number>>({});

  useEffect(() => {
    fetchQueryRunCounts(SAMPLES.map(s => s.text)).then(setRunCounts).catch(() => {});
  }, []);

  const filtered = SAMPLES.filter(s => {
    if (complexityFilter !== 'all' && s.complexity !== complexityFilter) return false;
    if (suiteFilter !== 'all' && s.suite !== suiteFilter) return false;
    return true;
  });

  const copy = (text: string, idx: number) => {
    navigator.clipboard.writeText(text);
    setCopied(idx);
    setTimeout(() => setCopied(null), 1500);
  };

  const complexityCounts = {
    low: SAMPLES.filter(s => s.complexity === 'low').length,
    medium: SAMPLES.filter(s => s.complexity === 'medium').length,
    high: SAMPLES.filter(s => s.complexity === 'high').length,
  };

  const suiteCounts = {
    core: SAMPLES.filter(s => s.suite === 'core').length,
    'x-plane': SAMPLES.filter(s => s.suite === 'x-plane').length,
    'proto-ready': SAMPLES.filter(s => s.suite === 'proto-ready').length,
    activation: SAMPLES.filter(s => s.suite === 'activation').length,
    'eval-corpus': SAMPLES.filter(s => s.suite === 'eval-corpus').length,
  };

  return (
    <div className="sample-queries">
      <h2>Sample Queries</h2>
      <p className="sample-queries-intro">
        Pre-built queries for testing the neuron scoring pipeline. Click any query to copy it to clipboard, then paste into the Query Lab.
      </p>

      {/* Suite filter */}
      <div className="sample-filter-bar">
        <span style={{ color: '#888', fontSize: '0.8rem', marginRight: 4 }}>Suite:</span>
        {(['all', 'eval-corpus', 'core', 'x-plane', 'proto-ready', 'activation'] as const).map(suite => (
          <button
            key={suite}
            className={`sample-filter-btn${suiteFilter === suite ? ' active' : ''}`}
            style={suite !== 'all' ? { '--filter-color': SUITE_CONFIG[suite].color } as React.CSSProperties : undefined}
            onClick={() => setSuiteFilter(suite)}
          >
            {suite === 'all' ? `All (${SAMPLES.length})` : `${SUITE_CONFIG[suite].label} (${suiteCounts[suite]})`}
          </button>
        ))}
      </div>

      {/* Complexity filter */}
      <div className="sample-filter-bar">
        <span style={{ color: '#888', fontSize: '0.8rem', marginRight: 4 }}>Complexity:</span>
        {(['all', 'low', 'medium', 'high'] as const).map(level => (
          <button
            key={level}
            className={`sample-filter-btn${complexityFilter === level ? ' active' : ''}`}
            style={level !== 'all' ? { '--filter-color': COMPLEXITY_COLORS[level] } as React.CSSProperties : undefined}
            onClick={() => setComplexityFilter(level)}
          >
            {level === 'all' ? `All (${SAMPLES.length})` : `${level.charAt(0).toUpperCase() + level.slice(1)} (${complexityCounts[level]})`}
          </button>
        ))}
      </div>

      <div style={{ color: '#888', fontSize: '0.85rem', margin: '4px 0 12px' }}>
        Showing {filtered.length} of {SAMPLES.length} queries
      </div>

      <div className="sample-list">
        {filtered.map((q) => {
          const globalIdx = SAMPLES.indexOf(q);
          const suiteInfo = SUITE_CONFIG[q.suite];
          return (
            <div
              key={globalIdx}
              className={`sample-card${copied === globalIdx ? ' copied' : ''}`}
              onClick={() => copy(q.text, globalIdx)}
            >
              <div className="sample-card-header">
                <div style={{ display: 'flex', gap: 6 }}>
                  <span
                    className="sample-complexity-badge"
                    style={{ background: COMPLEXITY_COLORS[q.complexity] }}
                  >
                    {q.complexity}
                  </span>
                  <span
                    className="sample-complexity-badge"
                    style={{ background: suiteInfo.color }}
                  >
                    {suiteInfo.label}
                  </span>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  {(runCounts[q.text] ?? 0) > 0 && (
                    <span style={{ color: '#888', fontSize: '0.75rem' }}>
                      Ran {runCounts[q.text]}×
                    </span>
                  )}
                  <span className="sample-copy-hint">
                    {copied === globalIdx ? 'Copied!' : 'Click to copy'}
                  </span>
                </div>
              </div>
              <p className="sample-text">{q.text}</p>
              <div className="sample-roles">
                {q.roles.map(r => (
                  <span key={r} className="sample-role-tag">{r}</span>
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
