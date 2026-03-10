;; Engineering Hub — org-capture templates (Doom Emacs compatible)
;;
;; Usage in Doom config.el:
;;
;;   (load! "~/dev/engineeringhub_controlinterface/config/engineering-hub-capture")
;;
;; Or paste the contents directly after your other (after! org ...) blocks.
;;
;; Prerequisites:
;;   - org-roam-dailies writes daily files to ~/org-roam/journal/YYYY-MM-DD.org
;;   - The journal capture template (key "j") already includes
;;     "* Overnight Agent Tasks" as a heading (which yours does).
;;
;; Capture keys added:
;;   "A"   Engineering Hub Task     — prompts for agent type
;;   "Ar"  Engineering Hub Research
;;   "Aw"  Engineering Hub Writing  (technical-writer)
;;   "Av"  Engineering Hub Review   (technical-reviewer)
;;   "As"  Engineering Hub Standards (standards-checker)
;;
;; Note: keys "e", "er", etc. are avoided here because your config already
;; uses "e" for an org-roam-capture-template.  If you prefer shorter keys,
;; change "A" to a letter not already in use.

;; ---------------------------------------------------------------------------
;; Helper functions — defined at top level so they are available immediately.
;; ---------------------------------------------------------------------------

(defvar eh/agents
  '("research" "technical-writer" "technical-reviewer" "standards-checker")
  "Valid Engineering Hub agent type strings.")

(defun eh/daily-journal-file ()
  "Absolute path to today's org-roam daily journal file."
  (expand-file-name
   (format-time-string "%Y-%m-%d.org")
   (expand-file-name "~/org-roam/journal/")))

(defun eh/ensure-overnight-heading ()
  "Position point ready for a new item inside * Overnight Agent Tasks.
Creates the heading at end of buffer when absent."
  (let ((heading "* Overnight Agent Tasks"))
    (goto-char (point-min))
    (if (re-search-forward (regexp-quote heading) nil t)
        (progn
          ;; Move past the heading line itself
          (end-of-line)
          ;; Find the start of the next top-level heading (or EOF)
          (let ((next-hdg (save-excursion
                            (and (re-search-forward "^\\* " nil t)
                                 (match-beginning 0)))))
            (if next-hdg
                (goto-char next-hdg)
              (goto-char (point-max))))
          ;; Back up over trailing blank lines so items are contiguous
          (skip-chars-backward "\n")
          (end-of-line)
          (insert "\n"))
      ;; Heading absent — append it
      (goto-char (point-max))
      (unless (bolp) (insert "\n"))
      (insert "\n" heading "\n"))))

;; ---------------------------------------------------------------------------
;; Capture templates — wrapped in (after! org) so org-capture-templates exists.
;; ---------------------------------------------------------------------------

(after! org
  (add-to-list 'org-capture-templates
    `("A" "Engineering Hub Agent Task" plain
      (file+function
       ,(lambda () (eh/daily-journal-file))
       eh/ensure-overnight-heading)
      "- [ ] @%(completing-read \"Agent type: \" eh/agents nil t): %?"
      :empty-lines 0
      :immediate-finish nil)
    t)

  (add-to-list 'org-capture-templates
    `("Ar" "Engineering Hub — Research" plain
      (file+function
       ,(lambda () (eh/daily-journal-file))
       eh/ensure-overnight-heading)
      "- [ ] @research: %?"
      :empty-lines 0
      :immediate-finish nil)
    t)

  (add-to-list 'org-capture-templates
    `("Aw" "Engineering Hub — Technical Writer" plain
      (file+function
       ,(lambda () (eh/daily-journal-file))
       eh/ensure-overnight-heading)
      "- [ ] @technical-writer: %?"
      :empty-lines 0
      :immediate-finish nil)
    t)

  (add-to-list 'org-capture-templates
    `("Av" "Engineering Hub — Technical Reviewer" plain
      (file+function
       ,(lambda () (eh/daily-journal-file))
       eh/ensure-overnight-heading)
      "- [ ] @technical-reviewer: %?"
      :empty-lines 0
      :immediate-finish nil)
    t)

  (add-to-list 'org-capture-templates
    `("As" "Engineering Hub — Standards Checker" plain
      (file+function
       ,(lambda () (eh/daily-journal-file))
       eh/ensure-overnight-heading)
      "- [ ] @standards-checker: %?"
      :empty-lines 0
      :immediate-finish nil)
    t))

;; ---------------------------------------------------------------------------
;; Convenience command — jump to today's Overnight Agent Tasks section.
;; ---------------------------------------------------------------------------

(defun eh/open-agent-tasks ()
  "Open today's journal and jump to the Overnight Agent Tasks heading."
  (interactive)
  (find-file (eh/daily-journal-file))
  (goto-char (point-min))
  (unless (re-search-forward "^\\* Overnight Agent Tasks" nil t)
    (goto-char (point-max))
    (insert "\n* Overnight Agent Tasks\n")))

(after! org
  (global-set-key (kbd "C-c h e") #'eh/open-agent-tasks))

;; ---------------------------------------------------------------------------
;; Example task formats
;; ---------------------------------------------------------------------------
;;
;;   - [ ] @research: Summarise ASTM E336 requirements for [[django://project/42]]
;;   - [ ] @technical-writer: Draft exec summary [[django://project/42]] → [[/outputs/docs/exec-42.md]]
;;   - [ ] @technical-reviewer: Arbitrate draft [[/path/to/draft.tex]] → [[/outputs/reviews/dr-42.md]]
;;   - [ ] @standards-checker: Verify E1007 compliance for [[django://project/42]]
;;
;; Using the inputs/ working directory (drop files in workspace/inputs/):
;;   - [ ] @research: Review brief [[django://project/42]] [[inputs/project-42/client-brief.pdf]]
;;   - [ ] @technical-writer: Draft report [[django://project/42]] [[inputs/project-42/site-data.md]] → [[/outputs/docs/report-42.md]]
;;   - [ ] @standards-checker: Check draft [[django://project/42]] [[inputs/project-42/draft-report.docx]]
;;   - [ ] @technical-reviewer: Review report [[django://project/42]] [[inputs/project-42/report-v1.docx]] → [[/outputs/reviews/review-42.md]]
;;
;; Org-roam internal cross-links ([[roam:SomePage]]) are automatically ignored
;; and will not be treated as file references.
