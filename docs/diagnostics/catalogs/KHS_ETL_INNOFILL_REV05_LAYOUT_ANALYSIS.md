# KHS ETL Innofill rev. 05: layout analysis

Source examined: `ETL_89401221_000400__Innofill_RU_05.pdf` (679 pages). Text was sampled from the front matter, early assemblies, middle pages, and pages 451, 501, 551, 601, and 651. Representative drawing and BOM pages were also rendered and visually inspected.

The PDF has a usable native text layer. The cover identifies Innofill DMG VF PET 160/15 RL, machine 47592. Pages 2-3 state revision 05 / 21.05.2019. Most technical content is a repeating KHS layout: a drawing/schematic page (often explicitly saying that the part number is in the specification) followed by one or more BOM pages. The latter have columns Position, Parts number, Description, Quantity, Unit, status flags, Remark, and Page. Their footer carries the current `Узел` (assembly) code and name; some drawing footers also show a parent assembly.

The parser uses the footer identity plus repeated row structure: numeric position, code, one or more description lines, decimal quantity, and unit. Russian name is retained as `part_name`; the German secondary name is staging metadata. Footer/header text is excluded. The code is not inferred from page number and there are no part-number-specific rules.

Pages 1-7 and the tail include cover, instructions, ordering form, and indexes/front matter. Pages without enough extractable text are recorded as `poor`; no OCR is performed. Pages with an explicit blank schematic notice are `drawing`; pages yielding structured rows are `parts_list`; remaining identified non-BOM pages are conservatively `mixed`, `front_matter`, or `unknown`.

Parent links are persisted only when a footer identifies a second assembly which also has an assembly page in this catalog. Otherwise the relationship remains NULL and becomes a parser warning.
